import requests
import json
import datetime
import os
from bs4 import BeautifulSoup
import re
# 定义发送企业微信机器人消息的函数
def send_wechat_message(webhook_url, message):
    headers = {"Content-Type": "application/json"}
    data = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }
    response = requests.post(webhook_url, headers=headers, json=data)
    if response.status_code == 200:
        print("消息发送成功")
    else:
        print(f"消息发送失败，状态码: {response.status_code}")
        print(f"响应内容: {response.text}")

# 目标URL
url = "https://ggzy.sc.yichang.gov.cn/EpointWebBuilder/rest/secaction/getSecInfoListYzm"

# 本地JSON文件路径
local_json_file = "zb.json"  # 绝对路径

# 企业微信机器人Webhook地址
webhook_url = os.environ["QYWX_URL"]
webhook_zb_url = os.environ["QYWX_ZB_URL"]
# 动态生成时间范围
today = datetime.datetime.now()
startdate = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
enddate = today.strftime("%Y-%m-%d 23:59:59")  # 修改为当天的23:59:59

# POST请求的表单数据
data = {
    "siteGuid": "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a",
    "categoryNum": "003001005",
    "content": "",
    "pageindex": "0",
    "pagesize": "6",
    "startdate": startdate,
    "enddate": enddate,
    "xiqucode": ""
}

# 发送POST请求
response = requests.post(url, data=data)

# 检查响应状态
if response.status_code == 200:
    # 解析JSON数据
    result = response.json()
    
    # 检查是否有数据
    if "custom" in result and "infodata" in result["custom"]:
        infodata = result["custom"]["infodata"]
        
        # 读取本地JSON文件
        if os.path.exists(local_json_file):
            try:
                with open(local_json_file, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
            except json.JSONDecodeError:
                print(f"本地JSON文件 {local_json_file} 格式不正确或为空，将重新创建。")
                local_data = []
        else:
            local_data = []
        
        # 检查并更新数据
        new_items = []
        for item in infodata:
            infourl = item.get("infourl", "")
            if not any(existing_item.get("infourl") == infourl for existing_item in local_data):
                # 新数据，添加到本地JSON文件
                local_data.append(item)
                new_items.append(item)
        
        # 如果有新数据，更新本地JSON文件
        if new_items:
            with open(local_json_file, "w", encoding="utf-8") as f:
                json.dump(local_data, f, ensure_ascii=False, indent=4)
            
            # 发送企业微信机器人消息
            for item in new_items:
                title = item.get("title", "")
                infourl = item.get("infourl", "")
                infodate = item.get("infodate", "")
                html = item.get("infocontent", "")
                soup = BeautifulSoup(html, 'html.parser')
                # 提取中标人
                bidder_td = soup.find('td', text=re.compile(r'中标人[:：]'))
                bidder = bidder_td.find_next_sibling('td').get_text(strip=True) if bidder_td else "未找到中标人信息"
                # 提取中标价
                price_td = soup.find('td', text=re.compile(r'中标价[\(（]?.*?[\)）]?[:：]'))
                price = price_td.find_next_sibling('td').get_text(strip=True) if price_td else "未找到中标价信息"
                # 拼接完整的链接
                full_url = f"https://ggzy.sc.yichang.gov.cn{infourl}"
                message = (
                    f"新公告：{title}\n"
                    f"日期：{infodate}\n"
                    f"中标人：{bidder}\n"
                    f"中标价：{price}\n"
                    f"链接：{full_url}\n"
                    f"注意：如果链接无法访问，可能是由于网络问题或链接本身的问题。请检查链接的合法性，并适当重试。"
                )
                send_wechat_message(webhook_url, message)
                if "盛荣" in bidder:
                    send_wechat_message(webhook_zb_url, message)
        else:
            print("没有新数据")
    else:
        print("未找到相关数据")
else:
    print(f"请求失败，状态码: {response.status_code}")
