from flask import Flask, request, abort, send_from_directory, render_template, jsonify, session
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import os
import yfinance as yf
import datetime
import matplotlib.pyplot as plt
import sqlite3
from dotenv import load_dotenv
import pandas as pd

# 加載 .env 文件
load_dotenv(dotenv_path='.env')

app = Flask(__name__)
app.secret_key = 'henryissmart'
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
host_url = os.getenv('host_url')
liff_id = os.getenv('LIFF_ID')

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 確保目錄存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 初始化資料庫
def init_db():
    conn = sqlite3.connect('user_favorites.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS favorites
                 (user_id TEXT, stock_code TEXT, PRIMARY KEY (user_id, stock_code))''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    return 'hello'

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip().upper()
    session['userid'] = user_id

    if text == 'LIKE':
        liff_url = f"https://liff.line.me/{liff_id}"


        line_bot_api.reply_message(
            event.reply_token,
            TemplateSendMessage(
                alt_text="請點擊以下連結添加收藏股票：",
                template=ButtonsTemplate(
                    text="請點擊以下連結添加收藏股票：",
                    actions=[
                        URIAction(label="添加收藏", uri=liff_url)
                    ]
                )
            )
        )
    elif text == 'STOCK':
        favorites = get_favorites(user_id)
        if not favorites:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您還沒有收藏任何股票"))
        else:
            carousel_columns = []
            for i in range(0, len(favorites), 3):
                chunk = favorites[i:i+3]
                actions = [PostbackAction(label=code, data="stock_code:"+code) for code in chunk]
                
                # 保證 actions 數量一致，少於 3 個時補空的按鈕
                while len(actions) < 3:
                    actions.append(MessageAction(label=" ", text=" "))
                carousel_columns.append(CarouselColumn(
                    title="我的收藏",
                    text="點擊查看詳情",
                    actions=actions
                ))
            carousel_template = CarouselTemplate(columns=carousel_columns)
            template_message = TemplateSendMessage(
                alt_text="我的收藏股票",
                template=carousel_template
            )
            line_bot_api.reply_message(event.reply_token, template_message)
    elif text == 'DELETE':
        # 使用快速回覆讓使用者選擇要刪除的股票
        favorites = get_favorites(user_id)
        if not favorites:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您還沒有收藏任何股票"))
        else:
            quick_reply_items = [QuickReplyButton(action=MessageAction(label=code, text=f"DELETE {code}")) for code in favorites]
            quick_reply = QuickReply(items=quick_reply_items)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請選擇要刪除的股票:", quick_reply=quick_reply))
    
    elif text.startswith('DELETE '):
        stock_code = text[7:]
        delete_favorite(user_id, stock_code)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"已將 {stock_code} 從您的收藏中刪除"))
    

@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data  # 取得 postback 資料

    # 根據 postback 資料執行相應操作
    if data.startswith('stock_code:'):
        stock_code = data.split(':')[1]  # 取得股票代碼
        try:
            messages = get_stock_info(stock_code)
            line_bot_api.reply_message(event.reply_token, messages)
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"查詢失敗，請確認股票代碼是否正確。錯誤信息: {str(e)}"))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text='無效的選擇，請重新嘗試。'))

@app.route('/add_favorites')
def add_favorites():
    return render_template('add_favorites.html', liff_id=liff_id)

@app.route('/api/add_favorites', methods=['POST'])
def api_add_favorites():
    try:
        data = request.json
        print(data)
        user_id = data.get('userId')
        print(user_id)
        stock_codes = data.get('stockCodes', [])
        
        # 檢查是否缺少 userId 或 stockCodes
        if not user_id:
            return jsonify({"success": False, "message": "缺少 userId"}), 400
        if not stock_codes:
            return jsonify({"success": False, "message": "缺少股票代碼 (stockCodes)"}), 400
        
        conn = sqlite3.connect('user_favorites.db')
        c = conn.cursor()
        
        # 確保 favorites 表存在
        c.execute('''CREATE TABLE IF NOT EXISTS favorites (
                        user_id TEXT,
                        stock_code TEXT,
                        UNIQUE(user_id, stock_code))''')
        
        for code in stock_codes:
            if is_valid_stock(code):
                c.execute("INSERT OR IGNORE INTO favorites (user_id, stock_code) VALUES (?, ?)", (user_id, code))
        print('..................................................')
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "收藏添加成功"})
    
    except Exception as e:
        return jsonify({"success": False, "message": f"發生錯誤: {str(e)}"}), 500

@app.route('/api/stocks', methods=['GET'])
def get_stock_symbols():
    symbols = []
    df = pd.read_csv(r'docs/stock_name.csv')
    for symbol in df['有價證券代號及名稱'][1:].tolist():
        if len(symbol) > 5:
            try:
                parts = symbol.split()
                code = parts[0]
                name = parts[1]
            except Exception:
                continue
            symbols.append({'code': code, 'name': name})
    return jsonify(symbols)


def get_favorites(user_id):
    conn = sqlite3.connect('user_favorites.db')
    c = conn.cursor()
    c.execute("SELECT stock_code FROM favorites WHERE user_id = ?", (user_id,))
    favorites = [row[0] for row in c.fetchall()]
    conn.close()
    return favorites

def delete_favorite(user_id, stock_code):
    conn = sqlite3.connect('user_favorites.db')
    c = conn.cursor()
    c.execute("DELETE FROM favorites WHERE user_id = ? AND stock_code = ?", (user_id, stock_code))
    conn.commit()
    conn.close()

def get_stock_info(ticker):
    six_months_ago = (datetime.datetime.now() - datetime.timedelta(days=180)).strftime('%Y-%m-%d')
    today = datetime.datetime.now().strftime('%Y-%m-%d')

    data = yf.Ticker(ticker + '.TW')
    df = data.history(start=six_months_ago, end=today)

    if df.empty:
        return [TextSendMessage(text=f"無法找到 {ticker} 的數據，請確認股票代碼是否正確。")]

    # 生成過去10天的價格文本
    close_price = df['Close'][-10:]
    dates = df.index[-10:]
    reply_text = f"{ticker} 最近 10 天收盤價:\n"
    reply_text += "\n".join([f"{date.strftime('%m-%d')} 收盤價為: {price:.2f} 元" for date, price in zip(dates, close_price)])

    # 生成半年的圖表
    image_url = save_plot(ticker, df.index, df['Close'])

    flex_message = FlexSendMessage(
                alt_text="股票資訊",
                contents={
                    "type": "bubble",
                    "hero": {
                        "type": "image",
                        "url": image_url,
                        "size": "full",
                        "aspect_ratio": "20:13",
                        "aspect_mode": "cover"
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"{ticker} 收盤價",
                                "weight": "bold",
                                "size": "xl"
                            },
                            {
                                "type": "separator",
                                "margin": "xxl"
                            },
                            {
                                "type": "box",
                                "layout": "vertical",
                                "margin": "xxl",
                                "spacing": "sm",
                                "contents": [
                                    {
                                        "type": "box",
                                        "layout": "baseline",
                                        "contents": [
                                            {
                                                "type": "text",
                                                "text": f"{date.strftime('%m-%d')}",
                                                "size": "sm",
                                                "color": "#555555",
                                                "flex": 0
                                            },
                                            {
                                                "type": "text",
                                                "text": f"${price:.2f}",
                                                "size": "sm",
                                                "color": "#111111",
                                                "align": "end"
                                            }
                                        ]
                                    }
                                    for date, price in zip(dates, close_price)
                                ]
                            },
                            {
                                "type": "separator",
                                "margin": "xxl"
                            },
                        ]
                    },
                    "styles": {
                        "footer": {
                            "separator": True
                        }
                    }
                }
            )


    return [flex_message]

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

def save_plot(ticker, dates, close_price):
    plt.figure(figsize=(10, 6))
    plt.plot(dates, close_price, marker='', linestyle='-', color='b')
    plt.xlabel('Date')
    plt.ylabel('Close Price')
    plt.title(f'{ticker} Close Prices (Last 6 Months)')
    plt.grid(True)

    image_filename = f"{ticker}_close_price.png"
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)

    plt.savefig(image_path)
    plt.close()

    image_url = f"{host_url}/uploads/{image_filename}"
    return image_url

def is_valid_stock(ticker):
    try:
        data = yf.Ticker(ticker +'.TW')
        df = data.history(period='1d')  # 獲取最近一天的數據來驗證股票代碼
        return not df.empty  # 若返回的數據非空，表示股票代碼有效
    except Exception:
        return False


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 9998))
    app.run(host='0.0.0.0', port=port, debug=True)