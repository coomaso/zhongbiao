import json
import requests
import datetime
import os
import re
from bs4 import BeautifulSoup
from typing import List, Dict

class BidMonitor:
    def __init__(self):
        self.original_file = "zb.json"  # 原始数据存储路径
        self.parsed_file = "parsed.json"      # 解析后数据存储路径

        # 环境变量配置 Webhook URL
        self.webhook_url = os.environ.get("QYWX_URL", "")
        self.webhook_zb_url = os.environ.get("QYWX_ZB_URL", "")
        
        # API 请求配置参数
        self.api_url = "https://ggzy.sc.yichang.gov.cn/EpointWebBuilder/rest/secaction/getSecInfoListYzm"
        self.site_guid = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
        self.category_num = "003001005"

    def fetch_data(self) -> List[Dict]:
        # 构造查询日期区间
        date_range = self._get_date_range(7)
        payload = {
            "siteGuid": self.site_guid,
            "categoryNum": self.category_num,
            "pageindex": "0",
            "pagesize": "6",
            **date_range
        }
        try:
            response = requests.post(self.api_url, data=payload)
            response.raise_for_status()
            return response.json().get("custom", {}).get("infodata", [])
        except requests.RequestException as e:
            print(f"API请求失败: {str(e)}")
            return []

    def process_data(self, new_data: List[Dict]) -> int:
        # 读取本地已有原始数据
        existing_raw = self._load_data_file(self.original_file)
        # 判断哪些是新增的数据
        new_items = [item for item in new_data if not self._is_existing(item, existing_raw)]

        if new_items:
            # 保存原始数据
            updated_raw = existing_raw + new_items
            self._save_data_file(self.original_file, updated_raw)

            # 解析新增数据并保存结构化内容
            parsed_data = self._load_data_file(self.parsed_file)
            parsed_items = [self._parse_and_link(item) for item in new_items]
            self._save_data_file(self.parsed_file, parsed_data + parsed_items)

        return len(new_items)

    def _parse_and_link(self, raw_item: Dict) -> Dict:
        # 将解析结果与原始数据建立关联
        parsed = {
            "infoid": raw_item.get("infoid"),
            "infourl": raw_item.get("infourl"),
            "data": self._parse_html_content(raw_item)
        }
        return parsed

    def send_notifications(self, new_items: List[Dict]):
        parsed_data = self._load_data_file(self.parsed_file)
        # 构建 infoid -> 解析结果 映射表
        parsed_map = {p["infoid"]: p for p in parsed_data}

        for raw_item in new_items:
            infoid = raw_item.get("infoid")
            parsed = parsed_map.get(infoid, {})

            # 优先使用结构化字段，否则回退原始HTML提取
            bidder = parsed.get("data", {}).get("中标人") or self._extract_from_html(raw_item, '中标人')
            price = parsed.get("data", {}).get("中标价") or self._extract_from_html(raw_item, '中标价')

            message = self._build_message(raw_item, bidder, price)
            self._send_wechat(message)

            # 特殊关键字额外通知
            if "盛荣" in bidder:
                self._send_wechat(message, is_special=True)

    def _parse_html_content(self, item: Dict) -> Dict:
        parsed = {}
        html = item.get("infocontent", "")
        try:
            soup = BeautifulSoup(html, 'html.parser')
            table = soup.find("table")  # 查找表格结构
            if table:
                for row in table.find_all("tr"):
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    self._process_row(cols, parsed)  # 提取键值对
        except Exception as e:
            parsed["error"] = str(e)
        return parsed

    def _process_row(self, columns: List[str], data: Dict):
        # 处理每一行表格中的列
        if len(columns) >= 2:
            key = self._clean_key(columns[0])
            data[key] = columns[1]
        if len(columns) >= 4:
            key2 = self._clean_key(columns[2])
            data[key2] = columns[3]

    def _clean_key(self, text: str) -> str:
        # 清洗字段名中的特殊符号
        return re.sub(r'[:：\s]', '', text).strip()

    def _extract_from_html(self, item: Dict, field: str) -> str:
        # 从原始 HTML 中提取字段（如中标人、中标价）
        html = item.get("infocontent", "")
        soup = BeautifulSoup(html, 'html.parser')
        td = soup.find('td', string=re.compile(fr'{field}[:：]'))
        return td.find_next_sibling('td').get_text(strip=True) if td else "未找到信息"

    def _build_message(self, item: Dict, bidder: str, price: str) -> str:
        # 构建通知消息文本
        return (
            f"新公告：{item.get('title', '')}\n"
            f"日期：{item.get('infodate', '')}\n"
            f"中标人：{bidder}\n"
            f"中标价：{price}\n"
            f"链接：{self._build_full_url(item.get('infourl', ''))}"
        )

    def _build_full_url(self, path: str) -> str:
        # 构造完整公告链接
        return f"https://ggzy.sc.yichang.gov.cn{path}" if path else ""

    def _send_wechat(self, message: str, is_special=False):
        # 发送企业微信消息通知
        webhook = self.webhook_zb_url if is_special else self.webhook_url
        payload = {"msgtype": "text", "text": {"content": message}}
        try:
            response = requests.post(webhook, json=payload)
            response.raise_for_status()
        except Exception as e:
            print(f"消息发送失败: {str(e)}")

    def _get_date_range(self, days: int) -> Dict:
        # 返回过去 `days` 天的时间范围
        today = datetime.datetime.now()
        return {
            "startdate": (today - datetime.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00"),
            "enddate": today.strftime("%Y-%m-%d 23:59:59")
        }

    def _is_existing(self, new_item: Dict, existing_data: List[Dict]) -> bool:
        # 判断数据是否已存在（根据infoid或url）
        return any(
            item.get("infoid") == new_item.get("infoid") or
            item.get("infourl") == new_item.get("infourl")
            for item in existing_data
        )

    def _load_data_file(self, filename: str) -> List[Dict]:
        # 加载指定JSON文件
        if not os.path.exists(filename):
            return []
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"加载 {filename} 失败: {str(e)}")
            return []

    def _save_data_file(self, filename: str, data: List[Dict]):
        # 保存数据到指定JSON文件
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"保存 {filename} 失败: {str(e)}")

if __name__ == "__main__":
    monitor = BidMonitor()
    fresh_data = monitor.fetch_data()

    if not fresh_data:
        print("未获取到新数据")
        exit(0)

    new_count = monitor.process_data(fresh_data)

    if new_count > 0:
        # 加载最新新增的原始数据
        raw_data = monitor._load_data_file(monitor.original_file)[-new_count:]
        monitor.send_notifications(raw_data)
        print(f"处理完成，新增{new_count}条数据")
    else:
        print("没有新增数据")
