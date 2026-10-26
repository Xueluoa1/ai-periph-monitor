"""Trace the .97 controls read path with a strict query-only transport gate."""
from pathlib import Path
import sys

if not getattr(sys,'frozen',False):
    sys.path.append(str(Path(__file__).resolve().parents[2]/'app'))
import am_controls as ac
import am_battery as am


class QueryDevice:
    def __init__(self,dev,emit):self.dev,self.emit=dev,emit
    def __getattr__(self,name):return getattr(self.dev,name)

    def set_feature_report(self,raw):
        if len(raw)<9 or raw[0]!=20 or raw[3:5]!=b'\x05\x5a':
            raise ValueError('诊断工具拒绝未知怒喵报文')
        cmd=int.from_bytes(raw[7:9],'little')
        sizes={ac.GET_RATE:0,ac.READ_NV:4,ac.GET_DONGLE:0,0x30C9:0}
        if cmd not in sizes or raw[1]!=6+sizes[cmd] or int.from_bytes(raw[5:7],'little')!=2+sizes[cmd]:
            raise ValueError('诊断工具仅允许怒喵读取命令')
        if cmd==ac.READ_NV and (raw[9:11] not in (b'\x0a\x60',b'\x00\x62') or raw[11:13]!=b'\xe8\x03'):
            raise ValueError('诊断工具拒绝未知 NV 地址')
        self.emit('tx',brand='angry_miao_controls',method='feature_query',hex=raw.hex())
        self.dev.set_feature_report(raw)

    def get_feature_report(self,*args):
        raw=self.dev.get_feature_report(*args)
        self.emit('rx',brand='angry_miao_controls',method='feature',hex=raw.hex())
        return raw


def probe(dev,iface,emit):
    if not am.matches_interface(iface):return {}
    try:
        state=ac.read(QueryDevice(dev,emit),iface)
        result={'polling_rate':{'hz':state.rate},'lighting':{
            'mouse_settings_raw':state.settings.hex() if state.settings else None,
            'mouse_pattern_raw':state.pattern.hex() if state.pattern else None,
            'receiver_raw':state.dongle.hex() if state.dongle else None,
            'errors':list(state.errors)}}
        emit('decoded',brand='angry_miao_controls',label='controls',value=result)
        return result
    except (OSError,ValueError) as exc:
        emit('query_error',brand='angry_miao_controls',label='controls',message=str(exc))
        return {}
