"""Bounded collection and export. Never invokes configuration setters."""
import ctypes
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
import hid_evidence


def desktop_directory():
    # CSIDL_DESKTOPDIRECTORY resolves redirected / OneDrive desktops as well.
    buf = ctypes.create_unicode_buffer(32768)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    fn = shell.SHGetFolderPathW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p]
    fn.restype = ctypes.c_long
    if fn(None, 0x10, None, 0, buf) != 0 or not buf.value:
        raise OSError('Windows 未能返回桌面目录')
    return Path(buf.value)


def read_events(path):
    events = []
    if path.exists():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append({'event': 'truncated_record', 'message': '进程中止时留下了不完整记录'})
    return events


def run_child(m, flags, folder, name, timeout):
    started = time.monotonic()
    # Direct files preserve stderr/stdout even if a driver hangs and gets killed.
    with (folder / (name + '.stdout.txt')).open('w', encoding='utf-8') as out, \
            (folder / (name + '.stderr.txt')).open('w', encoding='utf-8') as err:
        try:
            result = subprocess.run(m._self_cmd(*flags), stdout=out, stderr=err,
                                    timeout=timeout, creationflags=m.SUBPROCESS_NO_WINDOW)
            status = {'status': 'completed' if result.returncode == 0 else 'failed',
                      'exit_code': result.returncode}
        except subprocess.TimeoutExpired:
            status = {'status': 'timeout', 'timeout_seconds': timeout}
        except OSError as exc:
            status = {'status': 'failed', 'error': repr(exc)}
    status.update(worker=name, elapsed_seconds=round(time.monotonic() - started, 2))
    return status


def adaptation_report(folder, records, outcomes, m):
    result = {'schema_version': 2, 'tool_version': m.VERSION, 'build_id':getattr(m,'BUILD_ID',''), 'query_only': True,
              'write_support_verified': False, 'workers': outcomes, 'interfaces': [],
              'limitations': ['USB HID 接口不能单独证明连接为 2.4G。',
                              '读取值和解码候选不是写入能力验证；未读到不代表设备不支持。',
                              '灯光和回报率等字段仍需按具体型号、固件及官方协议核对。']}
    lines = ['多品牌外设诊断 v' + m.VERSION, '电量 / DPI / 回报率 / 灯光',
             '采集时间：' + datetime.now().astimezone().isoformat(timespec='seconds'),
             '', '本次不修改设备设置。请把完整 ZIP 交回进行适配。',
             '读取结果仅作候选；未验证任何写入功能。', '']
    inventory_events=read_events(folder/'inventory.jsonl')
    result['system_battery_evidence'] = read_events(folder / 'system_battery.jsonl')
    for event in result['system_battery_evidence']:
        lines.append('系统电量资料（蓝牙 / XInput；不与 USB 自动关联）：' + json.dumps(event, ensure_ascii=False))
    result['jbl_bluetooth_evidence']=[e for e in inventory_events if e.get('event') in ('jbl_bluetooth_evidence','jbl_bluetooth_error')]
    for event in result['jbl_bluetooth_evidence']:
        lines.append('JBL 蓝牙独立资料（不与 USB 自动关联）：'+json.dumps(event.get('devices',event.get('error')),ensure_ascii=False))
    for r in records:
        iid = r['interface_id']
        events = read_events(folder / (iid + '.jsonl'))
        summary = next((e['summary'] for e in reversed(events) if e.get('event') == 'interface_summary'), {})
        caps = summary.get('capabilities', {})
        if not summary:
            # A later query may time out after useful earlier replies were saved.
            caps = {e['label']: e['value'] for e in events
                    if e.get('event') == 'decoded' and e.get('label') in
                    ('battery', 'dpi', 'polling_rate', 'lighting') and 'value' in e}
        outcome = next((o for o in outcomes if o['worker'] == iid), {'status': 'inventory_only'})
        entry = {k: v for k, v in r.items() if k not in ('path', 'serial', 'group')}
        entry.update(worker_status=outcome['status'], probe_summary=summary,
                     capabilities={key: {'read_status': 'candidate' if key in caps else 'unconfirmed',
                                         'candidate_value': caps.get(key), 'write_status': 'not_tested'}
                                   for key in ('battery', 'dpi', 'polling_rate', 'lighting')})
        entry['query_status']=hid_evidence.query_status(events,summary,outcome['status'])
        entry['descriptor_collected']=any(e.get('event')=='hid_descriptor' for e in events)
        entry['passive_status']=next((e for e in reversed(events) if e.get('event')=='passive_status'),None)
        result['interfaces'].append(entry)
        lines.append(f"{r['product'] or r['brand_name']}  {r['vid_hex']}:{r['pid_hex']}  {r['usage_page_hex']}:{r['usage_hex']}")
        lines.append('  状态：' + outcome['status'] + ' | ' + iid)
        labels={'protocol_not_registered':'尚未收录协议，未发送猜测命令','query_no_response':'已发送查询，未收到回复',
                'query_response_unparsed':'已有原始回复，尚未解析出电量','raw_reports_only':'已采集原始报文，不能直接认作电量',
                'battery_candidate':'已得到电量候选值','not_queried':'未执行专用查询',
                'passive_no_report':'已监听厂商接口，未收到主动报文；电量协议尚未确认'}
        lines.append('  查询：'+labels.get(entry['query_status'],entry['query_status']))
        for key, label in [('battery', '电量'), ('dpi', 'DPI'), ('polling_rate', '回报率'), ('lighting', '灯光')]:
            lines.append('  ' + label + '：' + (json.dumps(caps[key], ensure_ascii=False) if key in caps else '未确认'))
    if not records:
        lines.append('未获取到设备清单。请查看 workers 中的失败或超时记录；仍可把本日志交回。')
    lines.extend(['', '后续适配说明：先核对型号、固件、原始请求与响应，再实现对应功能。',
                  '未知私有协议可能需要官方驱动资料，或每次仅改变一项设置的前后两份日志。',
                  'A/B 字节差异只能提供线索，不能独自证明配置写入指令。',
                  '日志包含设备型号、硬件 ID、驱动状态和协议原始报文，不会自动上传。',
                  '普通接口仅采集能力表，不监听键盘/鼠标输入；厂商专用输入短时采样。设备路径及明文序列号不导出。',
                  'HID 能力表来自 Windows 解析结果，不等同原始 USB 报告描述符字节；未取得的资料会标明。',
                  '原始厂商报文仍可能含设备标识。'])
    (folder / '适配数据.json').write_text(m.redact_username(json.dumps(result, ensure_ascii=False, indent=2)), encoding='utf-8')
    (folder / '诊断摘要.txt').write_text(m.redact_username('\n'.join(lines)), encoding='utf-8-sig')


def export_archive(folder, destination, m):
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / (folder.name + '.zip')
    partial = archive.with_suffix('.zip.partial')
    try:
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as z:
            for file in sorted(folder.iterdir()):
                if file.is_file() and not file.name.startswith('_'):
                    data = m.redact_username(file.read_text(encoding='utf-8', errors='replace'))
                    z.writestr(file.name, data.encode('utf-8'))
        partial.replace(archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive


def run(args, m):
    print(f'多品牌外设诊断 v{m.VERSION} — 电量 / DPI / 回报率 / 灯光', flush=True)
    print('请保持接收器连接，唤醒鼠标和键盘。采集不会修改设备设置。', flush=True)
    print('通常需要数十秒；接口较多或驱动超时时最多约 3 分钟。', flush=True)
    run_id = uuid.uuid4().hex[:8]
    name = '外设诊断日志_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + run_id
    outcomes, records = [], []
    with tempfile.TemporaryDirectory(prefix='peripheral_scan_') as tmp:
        folder = Path(tmp) / name
        folder.mkdir()
        meta = dict(tool='peripheral_diagnostic', version=m.VERSION, build_id=getattr(m,'BUILD_ID',''), schema_version=2, run_id=run_id,
                    created_utc=datetime.now(timezone.utc).isoformat(), os=str(sys.getwindowsversion()),
                    python=sys.version.split()[0], query_only=True, inventory_only=args.inventory_only,
                    frozen=bool(getattr(sys, 'frozen', False)))
        (folder / 'run.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        deadline = time.monotonic() + m.TOTAL_TIMEOUT_S
        try:
            flags = ['--worker', 'inventory', '--out', str(folder)]
            if args.all:
                flags.append('--all')
            for vid in args.vid or []:
                flags.extend(['--vid', vid])
            print('[1/3] 扫描外设接口…', flush=True)
            outcomes.append(run_child(m, flags, folder, 'inventory', m.INVENTORY_TIMEOUT_S))
            if (folder / '_interfaces.json').exists():
                records = json.loads((folder / '_interfaces.json').read_text(encoding='utf-8'))
            remaining = deadline - time.monotonic()
            if remaining >= 1:
                print('  采集 Windows 蓝牙电量和 XInput 手柄档位…', flush=True)
                outcomes.append(run_child(m, ['--worker', 'system', '--out', str(folder)],
                                          folder, 'system', min(15.0, remaining)))
            targets = sorted(records,key=lambda r:not r['should_probe']) if not args.inventory_only else []
            print(f'[2/3] 发现 {len(records)} 个接口，采集能力表并查询已匹配的控制接口…', flush=True)
            for i, r in enumerate(targets, 1):
                iid = r['interface_id']
                remaining = deadline - time.monotonic()
                if remaining < 1:
                    outcomes.append({'worker': iid, 'status': 'skipped_total_timeout'})
                    continue
                print(f"  {i}/{len(targets)}  {r['product'] or r['brand_name']} {r['usage_page_hex']}", flush=True)
                outcomes.append(run_child(m, ['--worker', iid, '--out', str(folder)], folder, iid,
                                          min(m.WORKER_TIMEOUT_S if r['should_probe'] else 5.0, remaining)))
        except Exception as exc:
            outcomes.append({'worker': 'orchestrator', 'status': 'failed', 'error': repr(exc)})
        print('[3/3] 保存完整日志…', flush=True)
        adaptation_report(folder, records, outcomes, m)
        destination = Path(args.output).resolve() if args.output else None
        try:
            archive = export_archive(folder, destination or desktop_directory(), m)
        except OSError as exc:
            # Preserve diagnostics when Desktop is protected/unavailable.
            print('桌面保存失败，改存备用目录：' + str(exc), flush=True)
            fallback = Path(tempfile.gettempdir()) / '外设诊断日志'
            archive = export_archive(folder, fallback, m)
        print('\n日志已生成：' + str(archive), flush=True)
        print('请把这个 ZIP 发回。程序运行完毕，无需其他操作。', flush=True)
        return 0 if outcomes and all(o['status'] in ('completed', 'inventory_only') for o in outcomes) else 2
