#!/usr/bin/env python3
"""Windows/desktop process-tree RSS monitor (optional dependency: psutil).

Examples:
  python tools/memory_monitor.py --pid 12345 --duration 3600 --interval 30
  python tools/memory_monitor.py --name MSRewardsAssistant --duration 1800

Collects only process memory metadata, never browser cookies or browsing history.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Measure the application AND child browser processes.')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--pid', type=int, help='main application PID')
    choice.add_argument('--name', help='case-insensitive process-name fragment')
    parser.add_argument('--duration', type=int, default=1800, help='sample duration in seconds (default: 1800)')
    parser.add_argument('--interval', type=float, default=30, help='sample interval in seconds (default: 30)')
    parser.add_argument('--csv', default='memory_samples.csv')
    args = parser.parse_args(argv)
    if args.duration <= 0 or args.interval <= 0:
        parser.error('duration and interval must be positive')
    try:
        import psutil
    except ImportError:
        print('请先安装 psutil：python -m pip install psutil', file=sys.stderr)
        return 2

    def find_root():
        if args.pid:
            return psutil.Process(args.pid)
        matches = []
        for proc in psutil.process_iter(['name', 'pid']):
            name = (proc.info.get('name') or '').lower()
            if args.name.lower() in name:
                matches.append(proc)
        if not matches:
            raise RuntimeError(f'未找到进程名包含 {args.name!r} 的进程')
        if len(matches) > 1:
            print('发现多个候选进程，请优先使用 --pid：',
                  ', '.join(f'{p.pid}:{p.name()}' for p in matches), file=sys.stderr)
        return matches[0]

    try:
        root = find_root()
    except (psutil.Error, RuntimeError) as exc:
        print(f'无法获取进程：{exc}', file=sys.stderr)
        return 1

    target = Path(args.csv)
    target.parent.mkdir(parents=True, exist_ok=True)
    top_peak = 0
    finish = time.monotonic() + args.duration
    with target.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['local_time', 'root_pid', 'root_rss_mib', 'tree_rss_mib',
                         'child_count', 'largest_child_pid', 'largest_child_name',
                         'largest_child_rss_mib'])
        while True:
            try:
                processes = [root] + root.children(recursive=True)
                samples = []
                for proc in processes:
                    try:
                        samples.append((proc.pid, proc.name(), proc.memory_info().rss))
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
                if not samples:
                    print('主进程已退出，停止采样')
                    break
                child = max(samples[1:], key=lambda x: x[2], default=(0, '', 0))
                tree = sum(row[2] for row in samples)
                top_peak = max(tree, top_peak)
                mib = 1024 * 1024
                now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
                writer.writerow([now, root.pid, f'{samples[0][2] / mib:.1f}',
                                 f'{tree / mib:.1f}', len(samples) - 1, child[0],
                                 child[1], f'{child[2] / mib:.1f}'])
                f.flush()
                print(f'{now} | main={samples[0][2]/mib:.1f} MiB | '
                      f'tree={tree/mib:.1f} MiB | child={len(samples)-1}', flush=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                print('无法继续读取主进程，停止采样')
                break
            if time.monotonic() >= finish:
                break
            time.sleep(min(args.interval, max(0.01, finish - time.monotonic())))
    print(f'最高观察到的进程树 RSS: {top_peak / (1024 * 1024):.1f} MiB；CSV: {target}')
    print('注：进程树 RSS 是子进程常驻集求和，部分共享页面可能重复计算；不是操作系统私有工作集。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
