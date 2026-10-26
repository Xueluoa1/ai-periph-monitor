"""Exact shared receiver plus CID/MID gates, and captured LED register decoder."""
MODEL='VXE R1 Pro Max'
IDENTITY=(2,27)


def matches(d):
    return ((d.vid,d.pid) in ((0x3554,0xF58A),(0x3554,0xF58C))
            and (d.usage_page,d.usage,d.input_len,d.output_len)==(0xFF02,2,17,17))


def lighting(raw):
    if len(raw)!=8 or any(raw[i:i+2]!=b'\0\0' and sum(raw[i:i+2])&255!=0x55 for i in (0,2,4,6)):
        raise ValueError('VXE 灯光配置校验失败')
    effect,bright,speed,enabled=raw[0],raw[2],raw[4],raw[6]
    if effect not in (0,1,2) or enabled not in (0,1) or speed not in (1,3,5):raise ValueError('未知 VXE 灯光代码')
    if raw[2:4]!=b'\0\0' and bright not in (0x10,0x80,0xFF):raise ValueError('未知 VXE 亮度代码')
    mode=0 if effect==0 or effect==1 and enabled==0 else effect
    return dict(kind='dpi_indicator',mode_code=mode,mode_name=('关闭','常亮','呼吸')[mode],
                brightness_level=None if raw[2:4]==b'\0\0' else (0x10,0x80,0xFF).index(bright)+1,
                speed_level=(1,3,5).index(speed)+1,raw=raw.hex())
