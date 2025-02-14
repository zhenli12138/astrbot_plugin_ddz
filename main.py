from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
import inspect
from pathlib import Path
import os
from queue import Queue
import random
from PIL import Image, ImageDraw, ImageFont
import time
from collections import defaultdict
'''
players: 玩家列表。


dizhu: 地主玩家。
dipai: 底牌（三张牌）。
current_bidder: 当前叫地主的玩家。
multiplier: 游戏倍数。
history: 游戏历史记录。

rankings: 玩家排名。
game_state: 游戏状态，如等待、叫地主、出牌等。
last_played: 上一个出牌的玩家。
bid_count: 叫地主次数。
open_cards: 明牌的玩家。
'''

# ========== 扑克牌生成模块 ==========
class Poker:
    suits = ['♠', '♥', '♦', '♣']
    values = ['3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A', '2']
    specials = ['BJ', 'RJ']
    colors = {'♠': (0, 0, 0), '♥': (255, 0, 0),
              '♦': (255, 0, 0), '♣': (0, 0, 0)}

@register("astrbot_plugin_ddz", "达莉娅", "ddz", "v0.1.0")
class MyPlugin(Star):
    # 在__init__中会传入Context 对象，这个对象包含了 AstrBot 的大多数组件
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.op = 0
        self.waiting_rooms = {}  # {creator_id: room_id}
        self.played_cards = []   # played_cards: 已经出过的牌。
        self.rankings = {}

        self.counter = 0
        self.config = config
        self.enabled = True                     # 初始化插件开关为关闭状态

        self.rooms = {}  # {room_id: game}
        self.player_rooms = {}  # {player_id: room_id}
    @filter.command("斗地主")
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def ddz_menu(self, event: AstrMessageEvent):
        img = self.generate_menu()
        yield event.make_result().message("斗地主游戏菜单：").file_image(img)

    @filter.command("创建房间")
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def create_room_cmd(self,event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        if user_id in self.player_rooms:
            yield event.plain_result("您已经在房间中！")
            return
        if room_id in self.rooms:
            yield event.plain_result(f"房间 {room_id} 已存在！")
            return
        room_id = self.create_room(user_id,event)
        yield event.plain_result(f"房间创建成功！房间号：{room_id}\n等待其他玩家加入...")

    def create_room(self, creator,event: AstrMessageEvent):
        room_id = event.get_group_id()
        self.player_rooms[creator] = room_id
        self.rooms[room_id] = {
            'players': [creator],
            'game': {'current_player':'',
                     'dipai':[],
                     'deck':[],
                     'hands':{},
                     'bid_count':int,
                     'dizhu':'',
                     'current_robber':'',
                     'current_bidder':'',
                     'last_played':{},},
            'state': 'waiting'
        }
        return room_id

    @filter.command("加入房间")
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def join_room_cmd(self,event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        logger.info(self.rooms)
        if room_id not in self.rooms:
            yield event.plain_result(f"房间 {room_id} 不存在！")
            return
        if user_id in self.rooms[room_id]['players']:
            yield event.plain_result(f"你已经加入房间 {room_id}！ ")
            return
        if len(self.rooms[room_id]['players']) == 3:
            yield event.plain_result(f"房间 {room_id} 人数已满！")
            return
        self.rooms[room_id]['players'].append(user_id)
        logger.info(self.rooms[room_id]['players'])
        self.player_rooms[user_id] = room_id
        yield event.plain_result(f"成功加入房间 {room_id}！当前人数：{len(self.rooms[room_id]['players'])}")

    @filter.command("开始游戏")
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def start_game(self,event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        if len(self.rooms[room_id]['players']) == 3:
            players = self.rooms[room_id]['players']
            logger.info(players)
            self.rooms[room_id]['state'] = "发牌阶段"
            logger.info(self.rooms[room_id]['state'])
            self.rooms[room_id]['game']['deck'] = self.generate_deck()
            deck =self.rooms[room_id]['game']['deck']# deck: 一副完整的扑克牌。
            random.shuffle(deck)
            self.rooms[room_id]['game']['hands'] = {p: sorted(deck[i * 17:(i + 1) * 17],
                                                              key=lambda x: self.card_value(x))
                                                    for i, p in enumerate(players)}
            self.rooms[room_id]['game']['dipai'] = deck[51:54]
            yield event.plain_result(f"发牌结束，请私聊bot【/查看手牌】看牌！")
            self.rooms[room_id]['state'] = "叫地主阶段"
            logger.info(self.rooms[room_id]['state'])
            self.rooms[room_id]['game']['bid_count'] = 1
            self.rooms[room_id]['game']['current_bidder'] = random.choice(players)
            chain = [
                Plain("叫地主开始！当前叫牌玩家："),
                At(qq=self.rooms[room_id]['game']['current_bidder']),  # At 消息发送者
            ]
            yield event.chain_result(chain)
            self.op = 0
            idx = players.index(self.rooms[room_id]['game']['current_bidder']) + self.op
            self.rooms[room_id]['game']['current_robber'] = players[(idx + 1) % 3]
            chain = [
                Plain("抢地主阶段：请问你是否选择抢地主？"),
                At(qq=self.rooms[room_id]['game']['current_robber']),  # At 消息发送者
                Plain("发送【/抢地主】抢地主。"),
                Plain("发送【/不抢】不抢地主。"),
            ]
            yield event.chain_result(chain)
        else:
            yield event.plain_result(f"房间 {room_id}未满3人！当前人数：{len(self.rooms[room_id]['players'])}")

    #generate_deck: 类方法，生成一副完整的扑克牌，包括52张普通牌和2张特殊牌（大小王）。
    def generate_deck(self):
        deck = [f"{s}{v}" for v in Poker.values for s in Poker.suits]
        deck += Poker.specials
        return deck

    #card_value: 类方法，返回一张牌的数值大小，用于比较牌的大小。特殊牌（大小王）有更高的数值。
    def card_value(self, card):
        order = {'3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
                 '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13,
                 'A': 14, '2': 15, 'BJ': 16, 'RJ': 17}
        if card in Poker.specials:
            return order[card]
        return order[card[1:]]

    @filter.command("查看手牌")
    @event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def lookcard(self,event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        players = self.rooms[room_id]['players']
        if user_id in players:
            idx = players.index(user_id)
            hand_img = self.generate_hand_image(self.rooms[room_id]['game']['hands'][user_id],idx)
            message_chain = MessageChain().message("您的手牌为：").file_image(hand_img)
            logger.info(self.rooms[room_id]['game']['hands'][user_id])
            await event.send(message_chain)

    @filter.command('不抢')
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def process_bid1(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        players = self.rooms[room_id]['players']
        if user_id == self.rooms[room_id]['game']['current_bidder']:
            chain = [
                Plain("您已叫地主，当前地主玩家为"),
                At(qq=self.rooms[room_id]['game']['current_bidder']),  # At 消息发送者
            ]
            yield event.chain_result(chain)
            return
        elif user_id == self.rooms[room_id]['game']['current_robber']:
            self.rooms[room_id]['game']['bid_count'] += 1
            self.op =1
            chain = [
                Plain("您选择不抢地主"),
                At(qq=self.rooms[room_id]['game']['current_bidder']),  # At 消息发送者
            ]
            yield event.chain_result(chain)
            if self.rooms[room_id]['game']['bid_count'] == 3:
                self.rooms[room_id]['game']['dizhu'] = self.rooms[room_id]['game']['current_bidder']
                self.rooms[room_id]['game']['hands'][self.rooms[room_id]['game']['dizhu']].extend(self.rooms[room_id]['game']['dipai'])
                self.rooms[room_id]['game']['hands'][self.rooms[room_id]['game']['dizhu']].sort(key=lambda x: self.card_value(x))
                chain = [
                    At(qq=self.rooms[room_id]['game']['dizhu']),  # At 消息发送者
                    Plain("你是本局游戏的地主！"),
                ]
                yield event.chain_result(chain)
                self.rooms[room_id]['state'] = "playing"
                logger.info(self.rooms[room_id]['state'])
                user_id = event.get_sender_id()
                room_id = self.player_rooms.get(user_id)
                if not room_id:
                    return
                self.rooms[room_id]['game']['current_player'] = self.rooms[room_id]['game']['dizhu']
                yield event.plain_result("地主确定！游戏开始！")
                yield event.plain_result(f"当前玩家：{self.rooms[room_id]['game']['current_player']} 请出牌")
            else:
                idx = players.index(self.rooms[room_id]['game']['current_bidder']) + self.op
                self.rooms[room_id]['game']['current_robber'] = players[(idx + 1) % 3]
                chain = [
                    Plain("抢地主阶段：请问你是否选择抢地主？"),
                    At(qq=self.rooms[room_id]['game']['current_robber']),  # At 消息发送者
                    Plain("发送【/抢地主】抢地主。"),
                    Plain("发送【/不抢】不抢地主。"),
                ]
                yield event.chain_result(chain)
            return
        else:
            chain = [
                Plain("目前不是你的回合"),
                At(qq=user_id),  # At 消息发送者
            ]
            yield event.chain_result(chain)

    #process_bid: 处理玩家叫地主的操作。如果玩家选择叫地主，则成为地主，游戏进入出牌阶段。如果所有玩家都不叫地主，则重新开始游戏。
    @filter.command('抢地主')
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def process_bid2(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        players = self.rooms[room_id]['players']
        if user_id == self.rooms[room_id]['game']['current_bidder']:
            chain = [
                Plain("您已叫地主，当前地主玩家为"),
                At(qq=self.rooms[room_id]['game']['current_bidder']),  # At 消息发送者
            ]
            yield event.chain_result(chain)
            return
        elif user_id == self.rooms[room_id]['game']['current_robber']:
            self.rooms[room_id]['game']['bid_count'] += 1
            self.rooms[room_id]['game']['current_bidder'] = self.rooms[room_id]['game']['current_robber']
            chain = [
                Plain("您已抢地主，当前地主玩家为"),
                At(qq=self.rooms[room_id]['game']['current_bidder']),  # At 消息发送者
            ]
            yield event.chain_result(chain)
            if self.rooms[room_id]['game']['bid_count'] == 3:
                self.rooms[room_id]['game']['dizhu'] = self.rooms[room_id]['game']['current_bidder']
                self.rooms[room_id]['game']['hands'][self.rooms[room_id]['game']['dizhu']].extend(self.rooms[room_id]['game']['dipai'])
                self.rooms[room_id]['game']['hands'][self.rooms[room_id]['game']['dizhu']].sort(key=lambda x: self.card_value(x))
                chain = [
                    At(qq=self.rooms[room_id]['game']['dizhu']),  # At 消息发送者
                    Plain("你是本局游戏的地主！"),
                ]
                yield event.chain_result(chain)
                self.rooms[room_id]['state'] = "playing"
                logger.info(self.rooms[room_id]['state'])
                user_id = event.get_sender_id()
                room_id = self.player_rooms.get(user_id)
                if not room_id:
                    return
                self.rooms[room_id]['game']['current_player'] = self.rooms[room_id]['game']['dizhu']
                yield event.plain_result("地主确定！游戏开始！")
                yield event.plain_result(f"当前玩家：{self.rooms[room_id]['game']['current_player']} 请出牌,发送【/出牌 []】出牌。")
            else:
                idx = players.index(self.rooms[room_id]['game']['current_bidder']) + self.op
                self.rooms[room_id]['game']['current_robber'] = players[(idx + 1) % 3]
                chain = [
                    Plain("抢地主阶段：请问你是否选择抢地主？"),
                    At(qq=self.rooms[room_id]['game']['current_robber']),  # At 消息发送者
                    Plain("发送【/抢地主】抢地主。"),
                    Plain("发送【/不抢】不抢地主。"),
                ]
                yield event.chain_result(chain)
            return
        else:
            chain = [
                Plain("目前不是你的回合"),
                At(qq=user_id),  # At 消息发送者
            ]
            yield event.chain_result(chain)

    @filter.command('出牌')
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def handle_play(self,event: AstrMessageEvent,cards_str:str):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        players = self.rooms[room_id]['players']
        if not room_id:
            return
        if self.rooms[room_id]['game']['current_player'] != user_id:
            yield event.plain_result( "现在不是你的回合！")
            return
        # 处理出牌逻辑
        if not room_id:
            yield event.plain_result("你不在游戏中！")
            return
        if self.rooms[room_id]['state'] != "playing":
            yield event.plain_result("游戏尚未开始！")
            return
        if self.rooms[room_id]['game']['current_player'] != user_id:
            yield event.plain_result("现在不是你的回合！")
            return
        # 解析出牌
        parsed_cards = self.parse_cards(cards_str, self.rooms[room_id]['game']['hands'][user_id])
        if not parsed_cards:
            yield event.plain_result("出牌无效！请检查牌型或是否拥有这些牌")
            return
        # 获取牌型信息
        play_type = self.validate_type(parsed_cards)
        if not play_type[0]:
            yield event.plain_result("不合法的牌型！")
            return

        # 验证是否符合出牌规则
        if self.rooms[room_id]['game']['last_played']:
            # 需要跟牌的情况
            if len(parsed_cards) != len(self.rooms[room_id]['game']['last_played']['cards']):
                yield event.plain_result("出牌数量不一致！")
                return
            if not self.compare_plays(self.rooms[room_id]['game']['last_played']['type'], play_type):
                yield event.plain_result("出牌不够大！")
                return
        else:
            # 首出需要合法牌型
            if play_type[0] is None:
                yield event.plain_result("首出必须使用合法牌型！")
                return

        # 执行出牌
        for c in parsed_cards:
            self.rooms[room_id]['game']['hands'][user_id].remove(c)
        # self.played_cards.extend(parsed_cards)
        self.rooms[room_id]['game']['last_played'] = {
            'player': user_id,
            'cards': parsed_cards,
            'type': play_type
        }

        # 更新游戏状态
        yield event.plain_result(f"{user_id} 出牌：{' '.join(parsed_cards)}")
        idx = players.index(self.rooms[room_id]['game']['current_player'])
        hand_img = self.generate_hand_image(self.rooms[room_id]['game']['hands'][user_id],idx)

        # 检查是否获胜
        if not self.rooms[room_id]['game']['hands'][user_id]:
            # 判断胜负
            if user_id == self.rooms[room_id]['game']['dizhu']:
                result = "地主获胜！"
                winners = [user_id]
            else:
                result = "农民获胜！"
                winners = [p for p in players if p != self.rooms[room_id]['game']['dizhu']]

            # 生成结果图片
            # ranking_img = ImageGenerator.generate_ranking_image(game.rankings)
            yield event.plain_result(f"游戏结束！{result}")
            # await SendTo(game.players, f"[CQ:image,file=base64://{ranking_img}]")
            # 重置游戏
            self.rooms[room_id]['state'] = "ended"
            return

        # 传递出牌权
        idx = players.index(self.rooms[room_id]['game']['current_player'])
        next_players = players[idx+1:] + players[:idx+1]
        for p in next_players:
            if p != self.rooms[room_id]['game']['current_player'] and len(self.rooms[room_id]['game']['hands'][p]) > 0:
                self.rooms[room_id]['game']['current_player'] = p
                break
        chain = [
            Plain("轮到玩家:"),
            At(qq=self.rooms[room_id]['game']['current_player']),  # At 消息发送者
            Plain("发送【/出牌 []】出牌。"),
        ]
        yield event.chain_result(chain)

    @filter.command('pass')
    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def handle_pass(self,event: AstrMessageEvent):
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        room_id = group_id
        players = self.rooms[room_id]['players']
        if not room_id:
            return
        if self.rooms[room_id]['game']['current_player'] != user_id:
            yield event.plain_result( "现在不是你的回合！")
            return
        # 处理出牌逻辑
        if not room_id:
            yield event.plain_result("你不在游戏中！")
            return
        if self.rooms[room_id]['state'] != "playing":
            yield event.plain_result("游戏尚未开始！")
            return
        if self.rooms[room_id]['game']['current_player'] != user_id:
            yield event.plain_result("现在不是你的回合！")
            return
        # 只有不是首出才能不出
        if not self.rooms[room_id]['game']['last_played']:
            yield event.plain_result("首出不能选择不出！")
            return

        # 传递出牌权
        idx = players.index(self.rooms[room_id]['game']['current_player'])
        next_players = players[idx+1:] + players[:idx+1]
        passed = True
        for p in next_players:
            if p == self.rooms[room_id]['game']['last_played']['player']:
                # 一轮结束
                self.rooms[room_id]['game']['last_played'] = {}
                self.rooms[room_id]['game']['current_player'] = p
                chain = [
                    Plain("新一轮开始，轮到玩家:"),
                    At(qq=self.rooms[room_id]['game']['current_player']),  # At 消息发送者
                    Plain("发送【/出牌 []】出牌。"),
                ]
                yield event.chain_result(chain)
                passed = False
                break
            if p != self.rooms[room_id]['game']['current_player'] and len(self.rooms[room_id]['game']['hands'][p]) > 0:
                self.rooms[room_id]['game']['current_player'] = p
                chain = [
                    Plain("轮到玩家:"),
                    At(qq=self.rooms[room_id]['game']['current_player']),  # At 消息发送者
                    Plain("发送【/出牌 []】出牌。"),
                ]
                yield event.chain_result(chain)
                passed = False
                break
        '''
        if passed:
            game.last_played = {}
            game.current_player = game.last_played['player']
            await SendTo(room_id, "所有玩家选择不出，开始新一轮出牌")
        '''
    # ========== 图像生成模块 ==========

    def generate_menu(self):
        img = Image.new('RGB', (800, 600), (73, 109, 137))
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype('msyh.ttc', 24)
        menu = [
            "【斗地主游戏菜单】",
            "/创建房间",
            "/加入房间",
            "/开始游戏",
            "/明牌操作",
            "/不抢",
            "/抢地主",
            "/出牌 [牌组]",
            "/查看手牌",
            "/退出游戏"
        ]
        y = 50
        for line in menu:
            d.text((100, y), line, fill=(255, 255, 0), font=font)
            y += 40

        if self.counter == 20:
            self.counter = 0
        self.counter = self.counter + 1
        output_path = f"./data/plugins/astrbot_plugin_ddz/pic{self.counter}.png"
        img.save(output_path, format='PNG')
        return output_path

    def generate_hand_image(self, cards,idx):
        card_width = 80
        card_height = 120
        spacing = 90
        img = Image.new('RGB', (card_width + (len(cards) - 1) * spacing, card_height), (56, 94, 15))
        for i, card in enumerate(cards):
            if card in ['BJ', 'RJ']:
                color = (0, 0, 0) if card == 'BJ' else (255, 0, 0)
                card_img = Image.new('RGB', (card_width, card_height), (255, 255, 255))
                d = ImageDraw.Draw(card_img)
                x, y = 10, 0
                for char in 'JOKER':
                    # 获取字符的边界框
                    bbox = d.textbbox((x, y), char, font=ImageFont.truetype('msyh.ttc', 20))
                    char_width, char_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    # 绘制字符
                    d.text((x, y), char, fill=(0, 0, 0), font=ImageFont.truetype('msyh.ttc', 20))
                    # 调整 y 坐标
                    y += char_height + 5
            else:
                suit = card[0]
                value = card[1:]
                card_img = Image.new('RGB', (card_width, card_height), (255, 255, 255))
                d = ImageDraw.Draw(card_img)
                d.text((50, 60), suit, fill=Poker.colors[suit], font=ImageFont.truetype('arial.ttf', 50))
                d.text((5, 0), value, fill=(0, 0, 0), font=ImageFont.truetype('msyh.ttc', 40))

            img.paste(card_img, (i * spacing, 0))


        output_path = f"./data/plugins/astrbot_plugin_ddz/pic{idx}.png"
        img.save(output_path, format='PNG')
        return output_path

    # ========== 牌型验证模块 ==========
    def validate_play(last_cards, new_cards):
        # 实现完整的牌型验证和比较逻辑
        pass

    # ========== 游戏控制模块 ==========

    @filter.command("vitspro")
    async def switch(self, event: AstrMessageEvent):
        '''这是一个插件开关指令'''
        message_str = event.message_str # 用户发的纯文本消息字符串
        message_chain = event.get_messages() # 用户所发的消息的消息链
        logger.info(message_chain)
        user_name = event.get_sender_name()
        user_id = event.get_sender_id()
        chain1 = [
            At(qq=user_id),  # At 消息发送者
            Plain(f"\n插件已经启动"),
            Face(id=337),
            Image.fromURL("https://i0.hdslb.com/bfs/article/bc0ba0646cb50112270da4811799557789b374e3.gif@1024w_820h.avif"),  # 从 URL 发送图片
        ]
        chain2 = [
            At(qq=user_id),  # At 消息发送者
            Plain(f"\n插件已经关闭"),
            Face(id=337),
            Image.fromURL("https://i0.hdslb.com/bfs/article/bc0ba0646cb50112270da4811799557789b374e3.gif@1024w_820h.avif"),  # 从 URL 发送图片
        ]
        self.enabled = not self.enabled
        if self.enabled:
            yield event.chain_result(chain1)
        else:
            yield event.chain_result(chain2)
    '''---------------------------------------------------'''
# ========== 牌型验证模块 ==========
    def parse_cards(self, cards_str, hand):
        """将输入的字符串转换为标准化牌型"""
        card_map = {
            'bj': 'BJ', 'rj': 'RJ',
            'j': 'J', 'q': 'Q', 'k': 'K', 'a': 'A',
            '2': '2', '3': '3', '4': '4', '5': '5',
            '6': '6', '7': '7', '8': '8', '9': '9',
            '10': '10'
        }
        cards = []
        for c in cards_str.lower().split():
            if c in ['bj', 'rj']:
                cards.append(c.upper())
            else:
                suit = ''
                if c.startswith(('♠', 's')):
                    suit = '♠'
                elif c.startswith(('♥', 'h')):
                    suit = '♥'
                elif c.startswith(('♦', 'd')):
                    suit = '♦'
                elif c.startswith(('♣', 'c')):
                    suit = '♣'
                value = card_map.get(c.lstrip('♠♥♦♣shdclrt').lower())
                if not value: return None
                cards.append(f"{suit}{value}")

        # 验证是否全部在手牌中
        temp_hand = hand.copy()
        for c in cards:
            if c not in temp_hand:
                return None
            temp_hand.remove(c)
        return cards


    def validate_type(self, cards):
        """验证牌型并返回类型和权重"""
        values = [self.card_value(c) for c in cards]
        values.sort()
        count = len(values)

        # 火箭
        if set(cards) == {'BJ', 'RJ'}:
            return ('rocket', 17)

        # 炸弹
        if count == 4 and len(set(values)) == 1:
            return ('bomb', values[0])

        # 单牌
        if count == 1:
            return ('single', values[0])

        # 对子
        if count == 2 and len(set(values)) == 1:
            return ('pair', values[0])

        # 三张
        if count == 3 and len(set(values)) == 1:
            return ('triple', values[0])

        # 三带一
        if count == 4:
            counter = defaultdict(int)
            for v in values:
                counter[v] += 1
            if sorted(counter.values()) == [1, 3]:
                return ('triple_plus_single', max(k for k, v in counter.items() if v == 3))
            if sorted(counter.values()) == [2, 3]:
                return ('triple_plus_pair', max(k for k, v in counter.items() if v == 3))

        # 单顺（至少5张）
        if count >= 5 and all(values[i] == values[i - 1] + 1 for i in range(1, count)):
            if max(values) < 15:  # 2不能出现在顺子中
                return ('straight', max(values))

        # 双顺（至少3对）
        if count >= 6 and count % 2 == 0:
            pairs = [values[i] for i in range(0, count, 2)]
            if all(pairs[i] == pairs[i - 1] for i in range(1, len(pairs))) and \
                    all(pairs[i] == pairs[i - 1] + 1 for i in range(1, len(pairs))) and \
                    max(pairs) < 15:
                return ('double_straight', max(pairs))

        # 飞机（至少2组三张）
        if count >= 6 and count % 3 == 0:
            triples = [values[i] for i in range(0, count, 3)]
            if all(triples[i] == triples[i - 1] for i in range(1, len(triples))) and \
                    all(triples[i] == triples[i - 1] + 1 for i in range(1, len(triples))) and \
                    max(triples) < 15:
                return ('airplane', max(triples))

        # 四带二
        if count == 6:
            counter = defaultdict(int)
            for v in values:
                counter[v] += 1
            if 4 in counter.values():
                quad_value = max(k for k, v in counter.items() if v == 4)
                return ('quad_plus_two', quad_value)

        return (None, 0)

    def compare_plays(self, last_type, new_type):
        """比较两次出牌的大小"""
        type_order = ['single', 'pair', 'triple', 'straight',
                      'double_straight', 'airplane', 'triple_plus_single',
                      'triple_plus_pair', 'quad_plus_two', 'bomb', 'rocket']

        # 特殊牌型比较
        if last_type[0] == 'rocket':
            return False
        if new_type[0] == 'rocket':
            return True
        if last_type[0] == 'bomb' and new_type[0] == 'bomb':
            return new_type[1] > last_type[1]
        if last_type[0] == 'bomb' and new_type[0] != 'bomb':
            return False
        if new_type[0] == 'bomb':
            return True

        # 普通牌型比较
        if last_type[0] != new_type[0]:
            return False
        return new_type[1] > last_type[1]






