"""Nordic M3 Pro battery query, independently implemented from official 1.6.7.11.

See 适配说明-首次完整采集.md for binary-analysis provenance and hardware limits.
Only GET state 0x81 is sent. Shared receiver identity is checked before mouse query.
"""
import time

MODEL='达摩鲨 Darmoshark M3 Pro'


def matches(d):
    return ((d.vid,d.pid)==(0x1915,0x0723)
            and (d.usage_page,d.usage,d.input_len,d.output_len)==(0xFF0A,1,65,65))


def request(target):
    if target not in (1,0x41):raise ValueError('未知达摩鲨查询目标')
    body=bytearray(64)
    body[:4]=bytes((target,0,0x81,1))
    body[63]=(0xA1-sum(body[:63]))&255
    return b'\0'+body


def body(raw,target):
    r=bytes(raw or b'')
    if len(r)!=65 or r[0]!=0:return None
    b=r[1:]
    if b[0]!=target or b[2]!=0x8C:return None
    return b


def exchange(dev,target,emit):
    packet=request(target)
    dev.drain()
    emit('tx',brand='darmoshark_m3pro',label='receiver_status' if target==1 else 'mouse_status',hex=packet.hex())
    dev.write_output(packet,use_hidd=False)
    deadline=time.monotonic()+1.0
    while time.monotonic()<deadline:
        raw=dev.read_input(timeout_ms=100,length=65)
        if not raw:continue
        emit('rx',brand='darmoshark_m3pro',label='status',hex=raw.hex())
        parsed=body(raw,target)
        if parsed is not None:return parsed,raw
    return None,b''


def read(dev,d,emit=lambda *a,**k:None):
    if not matches(d):raise ValueError('达摩鲨接口不匹配')
    receiver,raw=exchange(dev,1,emit)
    if receiver is None:return None,b''
    model_id=int.from_bytes(receiver[11:13],'little')
    emit('decoded',brand='darmoshark_m3pro',label='receiver_identity',
         value={'model_id':model_id,'connected':bool(receiver[4]&8)})
    if model_id!=0x0711 or not receiver[4]&8:return None,raw
    mouse,raw=exchange(dev,0x41,emit)
    if mouse is None:return None,b''
    if mouse[5] not in (0,1) or mouse[6]>100:return None,raw
    return (mouse[6],'charging' if mouse[5] else 'discharging',MODEL),raw
