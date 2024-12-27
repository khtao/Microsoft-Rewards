使用python来模拟PC端和移动端使用bing进行搜索，获取积分
在Ubuntu下测试通过，Windows系统也可以使用
# 使用方法
## 1、安装依赖库
pip install beautifulsoup4 selenium

## 2、修改用户数据目录
chrome_options.add_argument("--user-data-dir=" + "你的用户数据目录")

## 3、运行程序
python get_rewards.py

## 4、在弹出的窗口中登录微软账号
