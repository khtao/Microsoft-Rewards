# 引入相关库
import requests
from bs4 import BeautifulSoup
import random
from time import sleep
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from optparse import OptionParser


def top_list():
    # 1.定义要爬取的url连接
    url = 'https://top.baidu.com/board?tab=realtime'

    # 2.获取返回对象response，这里没有指定header信息，如果网站有反爬机制，请对应添加
    respose = requests.get(url)
    # 获取返回对象的HTML内容
    data = respose.text

    # 3.使用bs4解析对象
    soup = BeautifulSoup(data, "lxml")

    # 4.获取所需信息
    name_list = [x.get_text().strip().split(' ')[0] for x in soup.select('.title_dIF3B')]
    return name_list


def get_chengyu():
    data = open('chengyu.txt').readlines()
    all_data = []
    for dd in data:
        dd = dd.rstrip('\n')
        lls = dd.split('\u3000')
        for ll in lls:
            if len(ll) > 3:
                all_data.append(ll)
    return all_data


def get_args():
    parser = OptionParser()
    parser.add_option('--device', dest='device', default='pm', help='电脑端或者移动端进行搜索')

    (options, args) = parser.parse_args()
    return options


def edge_rewards(url, mobile=False):
    """使用Chrome浏览器访问bing.com 网站
    """
    search_num = 40
    # 启用Chrome浏览器参数配置
    chrome_options = webdriver.EdgeOptions()
    # 添加用户数据目录
    chrome_options.add_argument("--user-data-dir=" + "/home/khtao/.cache/Microsoft/Edge")
    if mobile:
        chrome_options.add_experimental_option('mobileEmulation', {'deviceName': 'iPhone X'})
        search_num = 30
    # 使用用户已有的缓存
    chrome_options.add_argument("--profile-directory=Default")
    #   设置为开发者模式、避免出现浏览器上提示 受到测试软件的控制
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])

    data = get_chengyu()[:search_num]+top_list()
    random.shuffle(data)
    # 创建webdriver对象
    driver = webdriver.Edge(options=chrome_options)
    driver.implicitly_wait(10)
    # 设置浏览器窗口大小
    if mobile:
        driver.set_window_size(200, 768)
    else:
        driver.set_window_size(1600, 768)

    for x in data[:search_num]:
        driver.get(url)
        sleep(random.uniform(120, 240))
        # 定位搜索框
        search_box = driver.find_element(By.ID, "sb_form_q")
        # 传入搜索关键词并搜索
        search_box.send_keys(x)
        search_box.send_keys(Keys.RETURN)
        sleep(random.uniform(60, 120))
        # 等待加载完成
        total = 0
        for i in range(200):  # 实现网页下拉
            num = random.randint(0, 25)
            total += num
            js = 'window.scrollTo(0,%s)' % total
            driver.execute_script(js)
            sleep(random.uniform(0, 0.2))

    driver.quit()


if __name__ == "__main__":
    args = get_args()
    if 'p' in args.device:
        edge_rewards('https://cn.bing.com/')
    if 'm' in args.device:
        edge_rewards('https://cn.bing.com/', True)
