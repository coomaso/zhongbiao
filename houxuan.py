import json
import requests
import datetime
import os
import re
import time
from bs4 import BeautifulSoup
from typing import List, Dict, Any
import traceback

class BidMonitor:
    def __init__(self):
        # 初始化文件路径
        self.original_file = "hx.json"
        self.parsed_file = "hx_parsed.json"
        
        # 企业微信配置
        self.webhook_url = os.getenv("QYWX_URL")
        self.webhook_zb_url = os.getenv("QYWX_ZB_URL")
        
        # 检查环境变量
        if not self.webhook_url:
            print("警告：QYWX_URL环境变量未设置，无法发送常规通知")
        if not self.webhook_zb_url:
            print("警告：QYWX_ZB_URL环境变量未设置，无法发送中标特别通知")
        
        # API配置
        self.api_url = "https://ggzy.sc.yichang.gov.cn/EpointWebBuilder/rest/secaction/getSecInfoListYzm"
        self.site_guid = "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a"
        self.category_num = "003001004"  # 中标候选人类别
        self.page_size = 6
        self.latest_new_count = 0  # 跟踪最新新增数量
        self.base_url = "https://ggzy.sc.yichang.gov.cn"  # 基础URL
        
    def _load_json_file(self, filename: str) -> List[Dict]:
        """加载JSON文件"""
        try:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            print(f"[文件错误] 加载 {filename} 失败: {str(e)}")
            return []

    def _save_json_file(self, filename: str, data: List[Dict]):
        """保存JSON文件"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[文件错误] 保存 {filename} 失败: {str(e)}")

    def _is_existing_record(self, new_item: Dict, existing: List[Dict]) -> bool:
        """检查记录是否已存在"""
        new_id = new_item.get("infoid")
        new_url = new_item.get("infourl")
        return any(
            item.get("infoid") == new_id or 
            item.get("infourl") == new_url
            for item in existing
        )
    
    def reparse_all_data(self):
        """重新解析所有原始数据"""
        original_data = self._load_json_file(self.original_file)
        parsed_data = []
        
        for item in original_data:
            parsed_record = {
                "infoid": item.get("infoid"),
                "infourl": item.get("infourl"),
                "parsed_data": self._parse_html_content(item),
                "raw_data": {
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        print(f"[重解析完成] 共解析 {len(parsed_data)} 条数据并保存到 {self.parsed_file}")

    def fetch_latest_data(self) -> List[Dict]:
        """获取最新招标数据"""
        payload = {
            "siteGuid": self.site_guid,
            "categoryNum": self.category_num,
            "pageindex": "0",
            "pagesize": str(self.page_size),
            "content": "",
            "startdate": "",
            "enddate": "", 
            "xiqucode": ""
        }
        
        for attempt in range(3):
            try:
                response = requests.post(self.api_url, data=payload, timeout=30)
                response.raise_for_status()
                return response.json().get("custom", {}).get("infodata", [])
            except requests.exceptions.Timeout:
                print(f"[第 {attempt+1} 次尝试] 请求超时")
            except requests.RequestException as e:
                print(f"[第 {attempt+1} 次尝试] 请求失败: {str(e)}")
            time.sleep(5)
        
        print("[最终失败] 无法获取数据")
        return []

    def process_and_store_data(self) -> int:
        """处理并存储数据"""
        new_raw_data = self.fetch_latest_data()
        if not new_raw_data:
            return 0

        existing_raw = self._load_json_file(self.original_file)
        new_items = [
            item for item in new_raw_data
            if not self._is_existing_record(item, existing_raw)
        ]
        
        if not new_items:
            return 0

        # 保存原始数据
        updated_raw = existing_raw + new_items
        self._save_json_file(self.original_file, updated_raw)
        
        # 解析新数据
        parsed_data = self._load_json_file(self.parsed_file)
        for item in new_items:
            parsed_record = {
                "infoid": item.get("infoid"),
                "infourl": item.get("infourl"),
                "parsed_data": self._parse_html_content(item),
                "raw_data": {
                    "title": item.get("title"),
                    "infodate": item.get("infodate")
                }
            }
            parsed_data.append(parsed_record)
        
        self._save_json_file(self.parsed_file, parsed_data)
        self.latest_new_count = len(new_items)  # 保存最新数量
        return self.latest_new_count

    def _parse_html_content(self, data: Dict) -> Dict:
        """解析HTML内容，提取项目信息和中标候选人列表"""
        project_name = ""
        try:
            project_name = data.get("customtitle", "").replace("中标候选人公示", "").strip()
            infocontent = data.get("infocontent", "")
            soup = BeautifulSoup(infocontent, 'html.parser')
            full_text = soup.get_text()

            # 提取公示时间 - 增强匹配逻辑
            publicity_period = ""
            pub_patterns = [
                r"公示[期时]为[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)",
                r"公示时间[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)",
                r"公示期[:：]?\s*(.+?至.+?)\s*(?:\n|<|$)",
                r"公示期为(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}时\d{1,2}分至\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}时\d{1,2}分)",
                r"公示期[为]?(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}时\d{1,2}分至\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}时\d{1,2}分)"
            ]
            for pattern in pub_patterns:
                match = re.search(pattern, full_text)
                if match:
                    if pattern in [pub_patterns[3], pub_patterns[4]]:  # 处理特定格式的时间
                        publicity_period = match.group(0).replace("公示期为", "").strip()
                    else:
                        publicity_period = match.group(1).strip()
                    break

            bidders_and_prices = []

            # 方法1：精确提取表格中的候选人及报价
            for table in soup.find_all('table'):
                header_found = False
                for row in table.find_all('tr'):
                    row_text = row.get_text(strip=True)
                    if any(keyword in row_text for keyword in ["中标候选人名称", "候选人名称", "单位名称", "名次"]):
                        header_found = True
                        
                        # 尝试从当前行或下一行提取候选人数据
                        candidate_row = row
                        # 如果当前行没有足够的单元格，尝试下一行
                        if len(row.find_all(['td', 'th'])) < 3:
                            candidate_row = row.find_next_sibling('tr')
                        
                        if candidate_row:
                            candidate_cells = candidate_row.find_all(['td', 'th'])
                            # 确定起始列：如果第一列包含"第一名"等，则从第一列开始
                            start_col = 0
                            # 检查第一列是否包含名次信息
                            if candidate_cells and re.match(r'^第?[一二三四五六七八九十\d]+名?$', candidate_cells[0].get_text(strip=True)):
                                start_col = 1  # 跳过名次列
                            
                            candidates = []
                            for i in range(start_col, len(candidate_cells)):
                                text = candidate_cells[i].get_text(strip=True)
                                # 排除空值、无关文本和名次文本
                                if (text and len(text) > 1 and 
                                    not re.match(r'^第?[一二三四五六七八九十\d]+名?$', text) and
                                    ("公司" in text or "集团" in text or "有限" in text or "设计院" in text)):
                                    candidates.append(text)
                        
                        # 查找包含"投标报价"的行
                        price_row = None
                        for next_row in row.find_next_siblings('tr'):
                            if any(keyword in next_row.get_text() for keyword in 
                                  ["投标报价", "报价", "投标总价", "总报价", "投标金额", "金额"]):
                                price_row = next_row
                                break
                        
                        if price_row:
                            price_cells = price_row.find_all(['td', 'th'])
                            prices = []
                            for i in range(start_col, len(price_cells)):
                                text = price_cells[i].get_text(strip=True)
                                # ═══ 修复：精确匹配纯标签格，不误杀含"报价"的实际数据 ═══
                                # 只跳过纯标签格（如"投标报价(元/%)"、"报价"等）
                                # 不跳过含实际数值的格（如"施工报价：折扣率96.18%；设计报价：546500.00元"）
                                if re.match(r'^(投标报价|报价|投标总价|总报价|投标金额|金额)\s*(\(.*?\))?\s*$', text):
                                    continue
                                if text and text != "/" and not re.match(r'^第?[一二三四五六七八九十\d]+名?$', text):
                                    prices.append(text)
                            
                            # 配对候选人和报价 - 确保数量匹配
                            for i, candidate in enumerate(candidates):
                                if i < len(prices):
                                    bidders_and_prices.append({
                                        "bidder": candidate,
                                        "price": prices[i]
                                    })
                                else:
                                    if i < len(price_cells):
                                        alt_price = price_cells[i].get_text(strip=True)
                                        bidders_and_prices.append({
                                            "bidder": candidate,
                                            "price": alt_price
                                        })
                                    else:
                                        bidders_and_prices.append({
                                            "bidder": candidate,
                                            "price": "未提供"
                                        })
                        
                        # 如果找到候选人，跳出循环
                        if bidders_and_prices:
                            break
                if header_found:
                    break

            # 方法2：如果表格提取失败，尝试从文本中提取
            if not bidders_and_prices:
                # 查找评审结果部分
                review_section = ""
                # 尝试多种可能的章节分隔
                section_patterns = [
                    r'二、评标结果(.+?)三、公示时间',
                    r'二、评标情况(.+?)三、公示时间',
                    r'二、评审结果(.+?)三、公示时间',
                    r'二、中标候选人(.+?)三、公示时间'
                ]
                for pattern in section_patterns:
                    review_match = re.search(pattern, full_text, re.DOTALL)
                    if review_match:
                        review_section = review_match.group(1)
                        break
                if not review_section:
                    review_section = full_text
                
                # 提取候选人名称 - 增强模式
                candidates = []
                # 模式1：匹配"第X中标候选人：公司名称"
                candidate_pattern1 = r'第[一二三四五六七八九十\d]+中标候选人[：:\s]*([^\n]+)'
                candidate_matches1 = re.findall(candidate_pattern1, review_section)
                if candidate_matches1:
                    candidates = [match.strip() for match in candidate_matches1]
                else:
                    # 模式2：匹配"中标候选人名称：公司A,公司B,公司C"
                    candidate_pattern2 = r'中标候选人名称[：:\s]*([^\n]+)'
                    candidate_match2 = re.search(candidate_pattern2, review_section)
                    if candidate_match2:
                        candidates_text = candidate_match2.group(1)
                        # 分割候选人名称
                        candidates = re.split(r'[、，,;；]', candidates_text)
                        # 清理空格
                        candidates = [c.strip() for c in candidates if c.strip()]
                    else:
                        # 模式3：直接查找排名不分先后的候选人
                        unordered_pattern = r'中标候选人为[（(]排名不分先后[）)]?[：:\s]*([^\n]+)'
                        unordered_match = re.search(unordered_pattern, review_section)
                        if unordered_match:
                            candidates_text = unordered_match.group(1)
                            # 分割候选人名称
                            candidates = re.split(r'[、，,;；]', candidates_text)
                            # 清理空格
                            candidates = [c.strip() for c in candidates if c.strip()]
                        else:
                            # 模式4：尝试提取表格外的候选人
                            table_candidates = []
                            for row in soup.find_all('tr'):
                                cells = row.find_all(['td', 'th'])
                                for cell in cells:
                                    text = cell.get_text(strip=True)
                                    if ("公司" in text or "集团" in text or "有限" in text) and len(text) > 5:
                                        if not any(c == text for c in table_candidates):
                                            table_candidates.append(text)
                            if table_candidates:
                                candidates = table_candidates
                            else:
                                # 备选方案：提取所有公司名称
                                company_pattern = r'([\u4e00-\u9fa5]{2,}(?:公司|集团|设计院|研究院|工程局|有限公司|股份公司))'
                                candidates = re.findall(company_pattern, review_section)
                                # 去重
                                seen = set()
                                unique_candidates = [c for c in candidates if c not in seen and not seen.add(c)]
                                candidates = unique_candidates
                
                # 提取报价 - 增强报价模式
                prices = []
                # 查找投标报价部分
                price_pattern = r'(?:投标报价|报价|投标总价|总报价)[：:\s]*([^\n]+?)(?:\n|$)'
                price_matches = re.findall(price_pattern, review_section)
                if price_matches:
                    # 从匹配的文本中提取具体的报价值
                    for match in price_matches:
                        # 尝试提取数字和单位
                        price_values = re.findall(r'([\d,.]+[万元%]?|[\d,.]+元|[\d.]+%)', match)
                        if price_values:
                            prices.extend(price_values)
                else:
                    # 备选方案1：提取百分比费率
                    rate_pattern = r'按.+?收费标准的(\d+)%'
                    rate_matches = re.findall(rate_pattern, review_section)
                    if rate_matches:
                        prices = [f"{rate}%" for rate in rate_matches]
                    else:
                        # 备选方案2：提取所有数字报价
                        price_pattern = r'([\d,.]+万元?|[\d,.]+元|[\d.]+%)'
                        prices = re.findall(price_pattern, review_section)
                
                # 配对候选人和报价
                for i, candidate in enumerate(candidates):
                    price = prices[i] if i < len(prices) else "未提供"
                    bidders_and_prices.append({
                        "bidder": candidate,
                        "price": price
                    })

            # 确保至少提取到3名候选人（如果原文有3名）
            if len(bidders_and_prices) < 3:
                # 尝试从表格中直接提取所有公司名称
                all_companies = []
                for table in soup.find_all('table'):
                    for row in table.find_all('tr'):
                        for cell in row.find_all(['td', 'th']):
                            text = cell.get_text(strip=True)
                            if ("公司" in text or "集团" in text) and len(text) > 5:
                                if not any(c == text for c in all_companies):
                                    all_companies.append(text)
                
                # 如果找到更多候选人，合并结果
                if len(all_companies) > len(bidders_and_prices):
                    for i, company in enumerate(all_companies):
                        if i >= len(bidders_and_prices):
                            # 为新发现的候选人添加默认报价
                            bidders_and_prices.append({
                                "bidder": company,
                                "price": "未提供"
                            })
                        else:
                            # 保留原始报价
                            bidders_and_prices[i]["bidder"] = company

            # 构建最终数据结构
            infourl = data.get("infourl", "")
            full_url = f"{self.base_url}{infourl}" if infourl.startswith("/") else infourl

            return {
                "project_name": project_name or "未知项目",
                "publicity_period": publicity_period,
                "bidders_and_prices": bidders_and_prices,
                "full_url": full_url
            }

        except Exception as e:
            print(f"[解析错误] {str(e)}")
            traceback.print_exc()
            return {
                "project_name": project_name or "解析失败",
                "publicity_period": "",
                "bidders_and_prices": [{"bidder": "解析失败", "price": "请查看详情"}],
                "full_url": ""
            }
            
    def _build_message(self, record: Dict) -> str:
        """构建通知消息"""
        try:
            parsed_data = record.get("parsed_data", {})
            raw_data = record.get("raw_data", {})
            
            # 构建中标候选人表格
            markdown_table = ""
            bap = parsed_data.get("bidders_and_prices", [])
            
            if bap:
                table_header = "|中标候选人|投标报价|\n| :----: | :------ |"
                table_rows = []
                
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "未提供").replace("&nbsp;", "").strip()
                    price = item.get("price", "未提供").replace("&nbsp;", "").strip()
                    
                    # 格式化报价 - 处理各种复杂情况
                    formatted_price = price
                    
                    # 情况1：纯数字（可能包含逗号）
                    if re.match(r'^[\d,]+(?:\.\d+)?$', price.replace(',', '')):
                        try:
                            # 移除逗号后转换为浮点数
                            price_num = float(price.replace(',', ''))
                            if price_num >= 1000000:  # 超过100万
                                formatted_price = f"{price_num/10000:,.2f}万元"
                            elif price_num >= 10000:  # 1万-100万
                                formatted_price = f"{price_num/10000:,.2f}万元"
                            else:
                                formatted_price = f"{price_num:,.2f}元"
                        except:
                            pass
                    
                    # 情况2：百分比费率
                    elif '%' in price:
                        # 保持原样显示
                        formatted_price = price
                    
                    # 情况3：包含"元"或"万元"
                    elif "元" in price or "万元" in price:
                        # 尝试提取数字部分进行格式化
                        num_match = re.search(r'([\d,\.]+)', price)
                        if num_match:
                            num_str = num_match.group(1).replace(',', '')
                            try:
                                num_val = float(num_str)
                                if "万元" in price or num_val >= 10000:
                                    formatted_price = f"{num_val/10000:,.2f}万元"
                                else:
                                    formatted_price = f"{num_val:,.2f}元"
                            except:
                                formatted_price = price
                    
                    # 情况4：复杂的文本描述（如按收费标准）
                    elif "按" in price and "标准" in price:
                        # 简化显示
                        simplified = re.sub(r'计费额以.*', '', price)
                        formatted_price = simplified.strip()
                    
                    table_rows.append(f"|{bidder}|{formatted_price}|")
                
                markdown_table = table_header + "\n" + "\n".join(table_rows)
            
            # 构建完整消息
            message = (
                "## 📢 中标候选人公告\n\n"
                f">**📜 标题**：{raw_data.get('title', '未知标题')}\n\n"
                f">**📅 日期**：{raw_data.get('infodate', '未知日期')}\n\n"
                f">**⏳ 公示时间**：{parsed_data.get('publicity_period', '未提供')}\n\n"
            )            
            if markdown_table:
                message += "**🏆 中标候选人及报价：**\n" + markdown_table + "\n\n"
            else:
                message += "**🏆 中标候选人：**\n"
                for i, item in enumerate(bap):
                    bidder = item.get("bidder", "未提供")
                    price = item.get("price", "未提供")
                    message += f"{i+1}. {bidder}"
                    if price and price != "未提供":
                        message += f" (报价: {price})"
                    message += "\n"
                message += "\n"
            
            # 添加候选人数量信息
            if bap:
                message += f"**共发现 {len(bap)} 名中标候选人**\n\n"
            
            message += f"🔗 **详情链接**：{parsed_data.get('full_url', '')}"
            
            return message
        except Exception as e:
            print(f"[消息构建错误] 构建通知消息失败: {str(e)}")
            traceback.print_exc()
            return ""

    def send_notifications(self):
        """发送通知"""
        if self.latest_new_count <= 0:
            return

        parsed_data = self._load_json_file(self.parsed_file)
        # 确保只处理当前新增的数据
        latest_parsed = parsed_data[-self.latest_new_count:]
        
        for record in latest_parsed:
            message = self._build_message(record)
            if not message:
                continue

            # 常规通知
            if self.webhook_url:
                self._send_wechat(message, self.webhook_url)
            
            # 检查是否有"盛荣"中标
            if "盛荣" in message:
                # 中标特别通知
                if self.webhook_zb_url:
                    self._send_wechat(f"【入围投标候选人通知】\n{message}", self.webhook_zb_url)

    def _send_wechat(self, message: str, webhook: str):
        """发送企业微信通知"""
        try:
            payload = {
                "msgtype": "markdown_v2",
                "markdown_v2": {
                    "content": message
                }
            }
            response = requests.post(webhook, json=payload, timeout=10)
            response.raise_for_status()
            print(f"[通知成功] 发送到 {webhook}")
        except Exception as e:
            print(f"[通知失败] {str(e)}")

if __name__ == "__main__":
    import sys
    monitor = BidMonitor()

    if "--reparse-all" in sys.argv:
        monitor.reparse_all_data()
    else:
        new_count = monitor.process_and_store_data()
        if new_count > 0:
            print(f"发现 {new_count} 条新公告")
            monitor.send_notifications()
        else:
            print("没有新数据需要处理")
