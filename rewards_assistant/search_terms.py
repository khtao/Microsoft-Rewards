"""搜索词建议：给**像人话的**查询词，由你本人粘贴到 Bing 里回车。

设计原则
--------
* 词库是真实的搜索主题（天气、评测、攻略、教程…），不是随机乱码 —— 后者正是
  微软判定自动化刷分的典型特征。
* 同一天不重复：用过的词记在 SQLite 里。
* 本模块只产出字符串，不碰浏览器、不碰网络。
"""
from __future__ import annotations

import random
from typing import Iterable, List, Sequence

TOPICS = {
    "生活": [
        "广州明天天气", "广州地铁11号线 站点", "快递物流查询 入口", "垃圾分类 对照表",
        "社保缴费基数 怎么算", "公积金提取 条件", "电费账单 网上查询", "宽带套餐 对比",
        "家电清洗 多久一次", "租房合同 注意事项", "换季衣物 收纳技巧", "宠物疫苗 时间表",
        "二手车 过户流程", "驾照换证 需要什么材料", "家庭急救箱 清单",
    ],
    "数码": [
        "RTX 5070 评测", "iPhone 17 参数对比", "固态硬盘 寿命 怎么看", "路由器 WiFi7 值得买吗",
        "机械键盘 轴体区别", "显示器 色域 怎么看", "NAS 入门 推荐", "笔记本 散热 改善",
        "Type-C 接口 协议区别", "蓝牙耳机 延迟 排行", "手机电池健康 恢复", "充电宝 额定容量",
        "雷电5 有什么提升", "智能手表 睡眠监测 准确度", "二手显卡 怎么验货",
    ],
    "学习": [
        "Python 生成器 用法", "SQL 窗口函数 教程", "线性代数 特征值 直觉", "英语听力 精听方法",
        "论文 文献综述 怎么写", "Git rebase 和 merge 区别", "正则表达式 贪婪匹配",
        "PySide6 信号槽 示例", "时间管理 番茄工作法", "康奈尔笔记法 怎么用",
        "考研数学 复习顺序", "雅思写作 常见错误", "数据结构 红黑树 讲解",
        "Linux 常用命令 速查", "Markdown 表格 语法",
    ],
    "旅行": [
        "成都三日游 攻略", "日本签证 材料清单", "高铁 选座 技巧", "云南 自驾 路线",
        "西安 美食 地图", "海岛 浮潜 注意事项", "民宿 预订 避坑", "旅行 行李打包 清单",
        "机票 特价 什么时候买", "欧洲 火车通票 划算吗", "重庆 轻轨 打卡点",
        "青海湖 最佳季节", "厦门 鼓浪屿 船票", "滑雪场 新手 推荐", "穷游 省钱 经验",
    ],
    "健康美食": [
        "番茄牛腩 做法", "空气炸锅 食谱 简单", "减脂餐 搭配 原则", "每天走多少步 合适",
        "拉伸 动作 顺序", "颈椎操 正确做法", "咖啡因 半衰期", "控糖 饮食 清单",
        "睡眠周期 90分钟", "跑步 膝盖疼 怎么办", "蛋白质 每天摄入量", "家常汤 推荐",
        "发酵面食 技巧", "低碳水 食谱", "维生素D 怎么补",
    ],
    "财经科技": [
        "指数基金 定投 方法", "国债逆回购 怎么买", "通货膨胀 对存款影响", "汇率 波动 原因",
        "AI 大模型 原理 入门", "量子计算 进展", "新能源车 电池 类型", "光伏 组件 效率",
        "商业航天 最新发射", "芯片制程 3nm 意义", "开源协议 区别", "云计算 三种服务模式",
        "网络安全 钓鱼识别", "碳中和 政策 解读", "半导体 周期 规律",
    ],
    "影视体育": [
        "豆瓣 高分 悬疑片", "纪录片 推荐 自然", "英超 积分榜", "NBA 交易 最新",
        "马拉松 训练计划", "羽毛球 高远球 技巧", "游泳 换气 要领", "台球 走位 基础",
        "露营 装备清单", "钓鱼 新手 入门", "电影 票房 排行", "播客 推荐 中文",
        "综艺 本季 播出时间", "足球 越位 规则", "健身房 新手 计划",
    ],
}

# 词库用尽时的自然后缀，保持“像人话”
_SUFFIXES = (" 怎么用", " 推荐", " 注意事项", " 2026最新", " 入门教程", " 是什么", " 对比")


def _base_pool() -> List[str]:
    pool = []
    for items in TOPICS.values():
        pool.extend(items)
    return pool


def suggest_terms(count: int, used: Sequence[str] = (), seed: int = 0) -> List[str]:
    """返回 count 个查询词，尽量避开 used 里已用过的。

    词库用尽时，在词条后追加自然后缀生成变体，保证仍然是通顺的搜索短语。
    """
    if count <= 0:
        return []
    rng = random.Random(seed or None)

    pool = _base_pool()
    rng.shuffle(pool)
    used_set = set(used)
    out: List[str] = []

    for term in pool:
        if len(out) >= count:
            break
        if term not in used_set:
            out.append(term)

    # 不够就加后缀变体
    if len(out) < count:
        extras = []
        for term in pool:
            for suffix in _SUFFIXES:
                variant = term + suffix
                if variant not in used_set and variant not in out:
                    extras.append(variant)
        rng.shuffle(extras)
        out.extend(extras[: count - len(out)])

    return out[:count]


def remaining_searches(points_left: int, points_per_search: int = 5) -> int:
    """按“每次搜索 5 分”估算还差多少次搜索。"""
    if points_per_search <= 0:
        return 0
    return max(0, -(-points_left // points_per_search))


def term_stats(terms: Iterable[str]) -> str:
    terms = list(terms)
    return f"今天已用 {len(terms)} 个词" if terms else "今天还没有用过搜索词"
