import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    # 解析 /start payload（深度链接携带的参数），形如 "/start abc123"
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else None

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="打开 PMSAuto 应用",
                    web_app=WebAppInfo(url="/app")
                )
            ]
        ]
    )

    intro = [
        "欢迎使用 PMSAuto 机器人！",
        "",
        "可用命令：",
        "/start - 欢迎与深链",
        "/help - 查看帮助",
        "/points - 查询积分（开发中）",
        "/register - 账号注册（开发中）",
        "",
        "也可以点击下方按钮打开 MiniApp。",
    ]
    if payload:
        intro.append("")
        intro.append(f"检测到深链参数：{payload}")

    await message.answer("\n".join(intro), reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "使用指引：\n"
        "- 通过 /register 在 MiniApp 中完成注册与绑定\n"
        "- 通过 /points 查看积分与到期信息（开发中）\n"
        "- 如遇问题，点击菜单打开 MiniApp 获取更多操作"
    )

@dp.message(Command("points"))
async def cmd_points(message: Message):
    # TODO: 调用后台 /admin/api/me 或根据 tg_id 查询
    await message.answer("暂未绑定账户，或积分功能开发中。")

@dp.message(Command("register"))
async def cmd_register(message: Message):
    # TODO: 引导到 MiniApp 页面进行注册/绑定
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开注册/绑定", web_app=WebAppInfo(url="/app"))]
        ]
    )
    await message.answer("请点击下方按钮打开 MiniApp 完成注册与绑定。", reply_markup=kb)
