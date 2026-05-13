import configparser
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from re import sub
from typing import Optional

import httpx
import requests
from openai import OpenAI
from urllib3 import disable_warnings, exceptions

from api.answer_check import *
from api.logger import logger

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

__all__ = ["CacheDAO", "Tiku", "TikuYanxi", "TikuLike", "TikuAdapter", "AI", "SiliconFlow", "EnsembleTiku"]

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
    """
    DEFAULT_CACHE_FILE = os.environ.get("CACHE_PATH", "cache.json")

    def __init__(self, file: str = DEFAULT_CACHE_FILE):
        self.cache_file = Path(file)
        self._lock = threading.RLock()
        if not self.cache_file.is_file():
            self._write_cache({})

    def _read_cache(self) -> dict:
        # 新增缓存文件读取的异常处理
        try:
            with self._lock:
                if not self.cache_file.is_file():
                    return {}
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        return json.load(fp)
                except json.JSONDecodeError as e:
                    logger.error(f"缓存文件 JSON 解析失败: {e}, 尝试恢复...")
                    # 尝试从原始二进制中以 utf-8 忽略错误地恢复有效 JSON 段
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end+1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # 若无法恢复，备份损坏文件并返回空缓存
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
                except UnicodeDecodeError as e:
                    logger.error(f"缓存文件编码读取失败: {e}, 采用恢复策略...")
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end+1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件编码错误，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
        except Exception as e:
            logger.error(f"读取缓存异常: {e}")
            return {}

    def _write_cache(self, data: dict) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                # 写入临时文件后原子替换，减少并发写入时的损坏风险
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=4)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                except Exception as e:
                    # 清理临时文件
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    logger.error(f"Failed to write cache atomically: {e}")
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str) -> Optional[str]:
        data = self._read_cache()
        return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        # 为缓存写入加锁，防止并发写入损坏文件
        with self._lock:
            data = self._read_cache()
            data[question] = answer
            self._write_cache(data)


# TODO: 重构此部分代码，将此类改为抽象类，加载题库方法改为静态方法，禁止直接初始化此类
class Tiku:
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")  # TODO: 从运行参数中获取config路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self) -> None:
        self._name = None
        self._api = None
        self._conf = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self,value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            self.COVER_RATE = float(self._conf['cover_rate'])
            self.true_list = self._conf['true_list'].split(',')
            self.false_list = self._conf['false_list'].split(',')
            # 调用自定义题库初始化
            self._init_tiku()

    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self,config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None
    def query(self,q_info:dict, course_context: str = None) -> Optional[str]:
        if self.DISABLE:
            return None

        # 预处理, 去除【单选题】这样与标题无关的字段
        logger.debug(f"原始标题：{q_info['title']}")
        q_info['title'] = sub(r'^\d+', '', q_info['title'])
        q_info['title'] = sub(r'（\d+\.\d+分）$', '', q_info['title'])
        logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.get_cache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info, course_context=course_context)
            if answer:
                answer = answer.strip()
                cache_dao.add_cache(q_info['title'], answer)
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
                if check_answer(answer, q_info['type'], self):
                    return answer
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
                    return None

            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None



    def _query(self,q_info:dict, course_context: str = None) -> Optional[str]:
        """
        查询接口, 交由自定义题库实现
        """
        pass


    def get_tiku_from_config(self):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        if not self._conf:
            # 尝试从默认配置文件加载
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        try:
            cls_name = self._conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
            return self
        try:
            new_cls = globals()[cls_name]()
        except KeyError:
            self.DISABLE = True
            logger.error(f"未知题库 provider '{cls_name}'，已禁用题库功能")
            return self
        new_cls.config_set(self._conf)
        return new_cls

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip()
        if answer in self.true_list:
            return True
        elif answer in self.false_list:
            return False
        else:
            # 无法判断, 随机选择
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True,False])

    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

# 按照以下模板实现更多题库

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0   # token队列计数器
        self._times = 100   # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self, q_info: dict, course_context: str = None):
        res = requests.get(
            self.api,
            params={
                'question':q_info['title'],
                'token': self._token,
                # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times",f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times",self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()

class TikuLike(Tiku):
    # Like知识库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'Like知识库'
        self.ver = '1.0.8' #对应官网API版本
        self.query_api = 'https://api.datam.site/search'
        self.balance_api = 'https://api.datam.site/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._token = None
        self._times = -1
        self._search = False
        self._count = 0

    def _query(self, q_info: dict, course_context: str = None):
        q_info_map = {"single":"【单选题】","multiple":"【多选题】","completion":"【填空题】","judgement":"【判断题】"}
        api_params_map = {0:"others",1:"choose",2:"fills",3:"judge"}
        q_info_prefix = q_info_map.get(q_info['type'],"【其他类型题目】")
        option_map = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7, 'a': 0, "b": 1, "c": 2, "d": 3,
                      "e": 4, "f": 5, "g": 6, "h": 7}
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n{options}"
        ret = ""
        ans = ""
        res = requests.post(
            self.query_api,
            json={
                'query': question,
                'token': self._token,
                'model': self._model if self._model else '',
                'search': self._search
            },
            verify=False
        )

        if res.status_code == 200:
            res_json = res.json()
            q_type = res_json['data'].get('type', 0)
            params = api_params_map.get(q_type, "")
            tans = res_json['data'].get(params, "")
            ans = ""
            match q_type:
                case 1:
                    for i in tans:
                        ans = ans + q_info['options'][option_map[i]] + '\n'
                case 2:
                    for i in tans:
                        ans = ans + i + '\n'
                case 3:
                    ans = "正确" if tans == 1 else "错误"
                case 0:
                    ans = tans
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
            return None

        ret += str(ans)

        self._times -= 1

        #10次查询后更新实际次数
        self._count = (self._count+1) % 10

        if self._count == 0:
            self.update_times()

        return ret

    def update_times(self):
        res = requests.post(
            self.balance_api,
            json={
                'token': self._token,
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            self._times = res_json["data"].get("balance",self._times)
            logger.info(f"当前LIKE知识库Token剩余查询次数为: {self._times}")
        else:
            logger.error('TOKEN出现错误，请检查后再试')

    def load_token(self):
        token = self._conf['tokens'].split(',')[-1] if ',' in self._conf['tokens'] else self._conf['tokens']
        self._token = token

    def load_config(self):
        self._search = self._conf['likeapi_search']
        self._model = self._conf['likeapi_model']
        var_params = {"likeapi_search": self._search, "likeapi_model": self._model}
        config_params = {"likeapi_search": False, "likeapi_model": None}

        for k,v in config_params.items():
            if k in self._conf:
                var_params[k] = self._conf[k]
            else:
                var_params[k] = v

    def _init_tiku(self):
        self.load_token()
        self.load_config()
        self.update_times()

class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict, course_context: str = None):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['bestAnswer']).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']

class AI(Tiku):
    # AI大模型答题实现
    def __init__(self) -> None:
        super().__init__()
        self.name = 'AI大模型答题'
        self.last_request_time = None

    def _query(self, q_info: dict, course_context: str = None):
        def remove_md_json_wrapper(md_str):
            # 使用正则表达式匹配Markdown代码块并提取内容
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()

        if self.http_proxy:
            proxy = self.http_proxy
            httpx_client = httpx.Client(proxy=proxy)
            client = OpenAI(http_client=httpx_client, base_url = self.endpoint,api_key = self.key)
        else:
            client = OpenAI(base_url = self.endpoint,api_key = self.key)
        # 去除选项字母，防止大模型直接输出字母而非内容
        options_list = q_info['options'].split('\n')
        cleaned_options = [re.sub(r"^[A-Z]\s*", "", option) for option in options_list]
        options = "\n".join(cleaned_options)

        # Rate limit BEFORE API call
        if self.last_request_time:
            interval_time = time.time() - self.last_request_time
            if interval_time < self.min_interval_seconds:
                sleep_time = self.min_interval_seconds - interval_time
                logger.debug(f"API请求间隔过短, 等待 {sleep_time} 秒")
                time.sleep(sleep_time)
        self.last_request_time = time.time()

        # 构建用户消息（含课程背景、搜索、裁判上下文）
        user_content = f"题目：{q_info['title']}"
        if q_info['type'] != 'completion' and q_info['type'] != 'judgement':
            user_content += f"\n选项：{options}"
        if course_context:
            user_content += f"\n当前课程：{course_context}，请结合该课程的知识领域回答问题。"
        if q_info.get('_search_context'):
            user_content += f"\n\n{q_info['_search_context']}"
        if q_info.get('_referee_context'):
            user_content += f"\n\n{q_info['_referee_context']}"

        # 判断题目类型
        if q_info['type'] == "single":
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "本题为单选题，你只能选择一个选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            )
        elif q_info['type'] == 'multiple':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "本题为多选题，你必须选择两个或以上选项，请根据题目和选项回答问题，以json格式输出正确的选项内容，示例回答：{\"Answer\": [\"答案1\",\n\"答案2\",\n\"答案3\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            )
        elif q_info['type'] == 'completion':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "本题为填空题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            )
        elif q_info['type'] == 'judgement':
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "本题为判断题，你只能回答正确或者错误，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            )
        else:
            completion = client.chat.completions.create(
                model = self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "本题为简答题，你必须根据语境和相关知识填入合适的内容，请根据题目回答问题，以json格式输出正确的答案，示例回答：{\"Answer\": [\"这是我的答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
                    },
                    {
                        "role": "user",
                        "content": user_content
                    }
                ]
            )

        try:
            response = json.loads(remove_md_json_wrapper(completion.choices[0].message.content))
            sep = "\n"
            return sep.join(response['Answer']).strip()
        except:
            logger.error("无法解析大模型输出内容")
            return None

    def _init_tiku(self):
        self.endpoint = self._conf.get('endpoint', '')
        self.key = self._conf.get('key', '')
        self.model = self._conf.get('model', '')
        self.http_proxy = self._conf.get('http_proxy', '')
        self.min_interval_seconds = int(self._conf.get('min_interval_seconds', 3))
class SiliconFlow(Tiku):
    """硅基流动大模型答题实现"""
    def __init__(self):
        super().__init__()
        self.name = '硅基流动大模型'
        self.last_request_time = None

    def _query(self, q_info: dict, course_context: str = None):
        def remove_md_json_wrapper(md_str):
            # 解析可能存在的JSON包装
            pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
            match = re.search(pattern, md_str, re.DOTALL)
            return match.group(1).strip() if match else md_str.strip()

        # 构造请求头
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # 构造系统提示词
        system_prompt = ""
        if q_info['type'] == "single":
            system_prompt = "本题为单选题，请根据题目和选项选择唯一正确答案，输出的是选项的具体内容，而不是内容前的ABCD，并以JSON格式输出：示例回答：{\"Answer\": [\"正确选项内容\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'multiple':
            system_prompt = "本题为多选题，请选择所有正确选项，输出的是选项的具体内容，而不是内容前的ABCD，以JSON格式输出：示例回答：{\"Answer\": [\"选项1\",\"选项2\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'completion':
            system_prompt = "本题为填空题，请直接给出填空内容，以JSON格式输出：示例回答：{\"Answer\": [\"答案文本\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        elif q_info['type'] == 'judgement':
            system_prompt = "本题为判断题，请回答'正确'或'错误'，以JSON格式输出：示例回答：{\"Answer\": [\"正确\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"
        else:
            system_prompt = "本题为简答题，请根据语境和相关知识给出答案，以JSON格式输出：示例回答：{\"Answer\": [\"答案\"]}。除此之外不要输出任何多余的内容，也不要使用MD语法。如果你使用了互联网搜索，也请不要返回搜索的结果和参考资料"

        # 构建用户消息（含课程背景、搜索、裁判上下文）
        user_content = f"题目：{q_info['title']}"
        if q_info['type'] != 'completion' and q_info['type'] != 'judgement':
            user_content += f"\n选项：{q_info['options']}"
        if course_context:
            user_content += f"\n当前课程：{course_context}，请结合该课程的知识领域回答问题。"
        if q_info.get('_search_context'):
            user_content += f"\n\n{q_info['_search_context']}"
        if q_info.get('_referee_context'):
            user_content += f"\n\n{q_info['_referee_context']}"

        # 构造请求体
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "stream": False,

            "max_tokens": 4096,

            "temperature": 0.7,
            "top_p": 0.7,
            "response_format": {"type": "text"}
        }

        # 处理请求间隔 — 在发送请求前限流
        if self.last_request_time:
            interval = time.time() - self.last_request_time
            if interval < self.min_interval:
                time.sleep(self.min_interval - interval)
        self.last_request_time = time.time()

        try:
            response = requests.post(
                self.api_endpoint,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                parsed = json.loads(remove_md_json_wrapper(content))
                return "\n".join(parsed['Answer']).strip()
            else:
                logger.error(f"API请求失败：{response.status_code} {response.text}")
                return None

        except Exception as e:
            logger.error(f"硅基流动API异常：{e}")
            return None

    def _init_tiku(self):
        # 从配置文件读取参数
        self.api_endpoint = self._conf.get('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
        self.api_key = self._conf.get('siliconflow_key', '')

        self.model_name = self._conf.get('siliconflow_model', 'deepseek-ai/DeepSeek-V3')


        self.min_interval = int(self._conf.get('min_interval_seconds', 3))


class EnsembleTiku(Tiku):
    """多模型协同答题 — 3 模型并行 + 联网搜索 + referee 裁决"""

    def __init__(self) -> None:
        super().__init__()
        self.name = '多模型协同答题'
        self._models: list[Tiku] = []
        self._search = None
        self._search_enabled = False
        self._referee_index = 0

    def _init_tiku(self):
        models_json = self._conf.get('models', '[]')
        try:
            model_configs = json.loads(models_json) if isinstance(models_json, str) else models_json
        except (json.JSONDecodeError, TypeError):
            logger.warning("多模型配置解析失败，回退到单模型")
            model_configs = []

        if not model_configs:
            self.DISABLE = True
            return

        for mc in model_configs:
            provider_name = mc.get('provider', '')
            if not provider_name:
                continue
            try:
                provider_cls = globals()[provider_name]
                inner = provider_cls()
                inner.config_set(mc)
                inner.init_tiku()
                if inner.DISABLE:
                    logger.warning(f"多模型: {provider_name} 初始化失败，已跳过")
                    continue
                self._models.append(inner)
            except KeyError:
                logger.warning(f"多模型: 未知 provider '{provider_name}'，已跳过")
            except Exception as e:
                logger.warning(f"多模型: {provider_name} 初始化异常: {e}")

        if not self._models:
            logger.warning("多模型: 没有可用模型，已禁用")
            self.DISABLE = True
            return

        self._search_enabled = self._conf.get('search_enabled', 'false') in ('true', 'True', '1', 'yes')
        self._referee_index = int(self._conf.get('referee_model_index', 0))
        if self._referee_index >= len(self._models):
            self._referee_index = 0

        if self._search_enabled:
            from api.web_search import create_search
            self._search = create_search(self._conf)

        logger.info(f"多模型协同初始化完成: {len(self._models)} 个模型, "
                     f"搜索={'启用' if self._search else '禁用'}, "
                     f"裁判模型={self._models[self._referee_index].name}")

    def _query(self, q_info: dict, course_context: str = None):
        # 1. 联网搜索
        search_context = ""
        if self._search_enabled and self._search:
            search_query = f"{course_context or ''} {q_info['title']}".strip()
            try:
                results = self._search.search(
                    query=search_query,
                    max_results=int(self._conf.get('search_max_results', 3)),
                )
                if results:
                    search_context = "\n网络搜索结果：\n" + "\n".join(
                        f"[{i + 1}] {r['title']}\n{r['snippet'][:300]}"
                        for i, r in enumerate(results)
                    )
            except Exception as e:
                logger.warning(f"搜索失败: {e}")

        # 2. 并行调用所有模型
        from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

        enriched = dict(q_info)
        if search_context:
            enriched['_search_context'] = search_context

        answers: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=len(self._models)) as executor:
            futures = {
                executor.submit(self._query_model, idx, model, enriched, course_context): idx
                for idx, model in enumerate(self._models)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    ans = future.result(timeout=30)
                    if ans:
                        answers.append((idx, ans.strip()))
                except FutureTimeout:
                    logger.warning(f"多模型: 模型 {idx} ({self._models[idx].name}) 超时")
                except Exception as e:
                    logger.warning(f"多模型: 模型 {idx} ({self._models[idx].name}) 异常: {e}")

        if not answers:
            logger.error("多模型: 所有模型均未返回答案")
            return None

        # 3. 收集唯一答案
        unique_answers = list({ans for _, ans in answers})
        if len(unique_answers) == 1:
            logger.info(f"多模型: 全票通过 -> {unique_answers[0]}")
            return unique_answers[0]

        # 4. 不一致 → referee 裁决
        logger.info(f"多模型: 答案不一致 {unique_answers}，进入 referee 裁决")
        return self._referee_vote(unique_answers, enriched, course_context)

    def _query_model(self, idx: int, model: Tiku, q_info: dict, course_context: str = None):
        """单个模型查询（供线程池调用）"""
        logger.debug(f"多模型: 模型 {idx} ({model.name}) 开始查询")
        return model._query(q_info, course_context=course_context)

    def _referee_vote(self, candidate_answers: list[str], q_info: dict, course_context: str = None):
        """裁判模型从候选答案中选择最佳答案"""
        referee = self._models[self._referee_index]

        # 构建裁判 prompt
        referee_q = dict(q_info)
        candidate_text = "\n".join(f"- {ans}" for ans in candidate_answers)
        referee_q['_referee_context'] = (
            f"以下其他模型给出了不同答案，请结合题目判断哪一个最正确，"
            f"直接输出该答案的完整内容：\n{candidate_text}"
        )

        try:
            ans = referee._query(referee_q, course_context=course_context)
            if ans:
                logger.info(f"多模型: referee 裁决 -> {ans.strip()}")
                return ans.strip()
        except Exception as e:
            logger.warning(f"多模型: referee 裁决异常: {e}")

        # fallback: 取第一个模型的答案
        logger.warning("多模型: referee 失败，采用第一个答案")
        return candidate_answers[0]
