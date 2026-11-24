"""1532:0550 YS-Tech/RACE voltage query. No estimated battery percentage.

Remote routing is temporary and always restored, even if entering times out.
Only E1 routing and GET_BATTERY are allowed; no firmware/configuration commands.
"""
import itertools
import time

_sequence=itertools.count(1)


def matches(d):
    return ((d.vid,d.pid)==(0x1532,0x0550) and
            (d.usage_page,d.usage,d.input_len,d.output_len,d.feature_len)==(0xFF00,1,64,64,0))


def packet(group,seq,body):
    if (group,body) not in ((0x0e,b'\x02\xe1\x01'),(0x0e,b'\x02\xe1\0'),(6,b'\x01\0\x31')):
        raise ValueError('不允许此 RACE 命令')
    r=bytearray(64);r[:3]=b'\x02\x80\x07'
    r[5:12]=bytes((0x50,0x41,group,seq))+body
    crc=0
    for value in r:
        crc^=value<<8
        for _ in range(8):crc=((crc<<1)^0x1021 if crc&0x8000 else crc<<1)&0xffff
    r[3:5]=crc.to_bytes(2,'little')
    return bytes(r)


def response(raw,group,seq):
    r=bytes(raw or b'')
    if len(r)!=64 or r[0]!=2 or not 10<=r[1]<=62:return None
    pos,end=2,2+r[1]
    while pos+10<=end:
        if r[pos:pos+2]!=b'\x50\x49':return None
        size=10+int.from_bytes(r[pos+8:pos+10],'little')
        if pos+size>end:return None
        f=r[pos:pos+size]
        if len(f)>=13 and f[10:12]==bytes((group,0x80|seq)):
            return f if f[12]==0 else None
        pos+=size
    return None


def exchange(dev,group,body):
    seq=next(_sequence)%127+1
    frame=packet(group,seq,body)
    for attempt in range(3):
        if attempt:time.sleep(.05)
        if dev.write_output(frame,use_hidd=False) is False:
            raise OSError('梭鱼 X 查询发送失败')
        ack=dev.get_input_report(2,64)
        if len(ack)!=64 or ack[0]!=2:return None
        if ack[1]==0x4f:break
        if ack[1]!=0x46:return None
    else:return None
    deadline=time.monotonic()+.8
    while time.monotonic()<deadline:
        parsed=response(dev.read_input(timeout_ms=100,length=64),group,seq)
        if parsed:return parsed
    return None


def read_voltage(dev,d):
    if not matches(d):raise ValueError('不是梭鱼 X 0550 的电量接口')
    from gravastar_k1 import report_ids
    import hid_win as hw
    if any(2 not in report_ids(dev,t) for t in (hw.HidP_Input,hw.HidP_Output)):
        raise ValueError('梭鱼 X 接口未声明报告 02')
    dev.drain()
    try:
        entered=exchange(dev,0x0e,b'\x02\xe1\x01')
        if entered is None:return None
        raw=exchange(dev,6,b'\x01\0\x31')
        if not raw or len(raw)<15:return None
        mv=int.from_bytes(raw[13:15],'little')
        return (mv,raw) if 2500<=mv<=4500 else None
    finally:
        if exchange(dev,0x0e,b'\x02\xe1\0') is None:
            raise OSError('梭鱼 X 临时查询通道退出未确认，请重新插拔接收器后重试')
