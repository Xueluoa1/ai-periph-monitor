"""Razer feature-report controls; effects are ACK-only, never claimed read back.

Independent implementation from OpenRazer protocol facts and OpenMouse's
per-model polling audit. No device mode, firmware or pairing commands.
"""
from dataclasses import dataclass, replace
from functools import reduce
from operator import xor
import time

R1=(125,500,1000)
R8=(125,500,1000,2000,4000,8000)
MODES={0:'关闭',1:'常亮',3:'色彩循环',4:'波浪'}
UNKNOWN_EFFECT=-999


@dataclass(frozen=True)
class Product:
    name: str
    tx: int
    rates: tuple = R1
    extended: bool = False
    commit: int | None = None
    lighting: str = ''
    interface: int = 0


PRODUCTS={}
for name,ids,tx in (
    ('Lancehead Wireless',(0x6F,0x70),0x3F),('Mamba Wireless',(0x72,0x73),0x3F),
    ('Pro Click',(0x77,0x80),0x1F),('Basilisk Ultimate',(0x86,0x88),0x1F),
    ('Naga Pro',(0x8F,0x90),0x1F),('DeathAdder V3 Pro',(0xB6,0xB7),0x1F),
    ('DeathAdder V3 HyperSpeed',(0xC4,0xC5),0x1F),('Viper V3 Pro SE',(0xDE,0xDF),0x1F)):
    for pid in ids:PRODUCTS[pid]=Product(name,tx)
for pid,name,tx in ((0x83,'Basilisk X HyperSpeed',0xFF),(0x94,'Orochi V2',0x1F),
                    (0x62,'Atheris',0x1F),(0x9A,'Pro Click Mini',0x1F),
                    (0x9C,'DeathAdder V2 X HyperSpeed',0x1F),(0xB4,'Naga V2 HyperSpeed',0x1F),
                    (0xB8,'Viper V3 HyperSpeed',0x1F),(0xB9,'Basilisk V3 X HyperSpeed',0x1F)):
    PRODUCTS[pid]=Product(name,tx)
for name,wired,receiver in (('Viper V2 Pro',0xA5,0xA6),('Naga V2 Pro',0xA7,0xA8),
                            ('Basilisk V3 Pro',0xAA,0xAB),('Cobra Pro',0xAF,0xB0)):
    PRODUCTS[wired]=Product(name+' USB',0x1F)
    PRODUCTS[receiver]=Product(name+' 2.4G',0x1F,extended=True)
PRODUCTS[0xC0]=Product('Viper V3 Pro USB',0x1F)
PRODUCTS[0xC1]=Product('Viper V3 Pro 2.4G',0x1F,R8,True,0x1F)
PRODUCTS[0xB2]=Product('DeathAdder V3',0x1F,R8,True,0x1F)
PRODUCTS[0xBE]=Product('DeathAdder V4 Pro USB',0x1F,R8,True)
PRODUCTS[0xBF]=Product('DeathAdder V4 Pro 2.4G',0x1F,R8,True)
PRODUCTS[0x91]=Product('Viper 8KHz',0xFF,R8,True)
PRODUCTS[0x99]=Product('Basilisk V3',0x1F,lighting='extended',interface=3)
PRODUCTS[0xCB]=Product('Basilisk V3 35K',0x1F,lighting='extended',interface=3)
PRODUCTS[0x4C]=Product('Diamondback Chroma',0xFF,lighting='standard')


def match(d):
    p=PRODUCTS.get(d.pid) if d.vid==0x1532 else None
    if p is None or f'mi_{p.interface:02x}' not in d.path.lower():return None
    # Windows uses the USB control interface, even when RID 0 is undeclared.
    if (d.usage_page,d.usage)==(1,2) or d.usage_page>=0xFF00:return p
    return None


def checksum(body):return reduce(xor,body[2:88],0)


def packet(tx,cls,command,size,args=()):
    if len(args)>size or not 0<=size<=80:raise ValueError('雷蛇请求长度错误')
    body=bytearray(90);body[1]=tx;body[5:8]=bytes([size,cls,command]);body[8:8+len(args)]=bytes(args)
    body[88]=checksum(body)
    return b'\0'+bytes(body)


def query(dev,p,cls,command,size,args=(),tx=None):
    tx=p.tx if tx is None else tx
    dev.set_feature_report(packet(tx,cls,command,size,args))
    for _ in range(10):
        time.sleep(.1)
        raw=dev.get_feature_report(0,91)
        if len(raw)!=91 or raw[0]!=0:continue
        body=raw[1:]
        if body[1]!=tx or body[6:8]!=bytes([cls,command]) or checksum(body)!=body[88]:continue
        if body[0] in (0,1):continue
        if body[0]!=2:raise ValueError(f'雷蛇固件拒绝命令 {cls:02X}/{command:02X}（状态 {body[0]:02X}）')
        if body[5]!=size:raise ValueError('雷蛇响应长度不匹配')
        return bytes(body[8:8+size])
    raise TimeoutError('雷蛇未返回有效响应，请唤醒鼠标并关闭占用接口的配置软件')


def read(dev,d,mouse):
    from peripheral_controls import State,Light
    p=match(d)
    if p is None:raise ValueError('雷蛇接口或型号未适配')
    serial=query(dev,p,0,0x82,22).split(b'\0',1)[0]
    if not serial or any(c<32 or c>126 for c in serial):raise ValueError('未确认鼠标身份')
    if p.extended:
        data=query(dev,p,0,0xC0,2,(0,));divisor=data[1];base=8000
        if data[0]!=0:raise ValueError('回报率存储区不匹配')
    else:
        divisor=query(dev,p,0,0x85,1)[0];base=1000
    if not divisor or base%divisor or base//divisor not in p.rates:raise ValueError('未知的雷蛇回报率')
    light=None;error='';raw=b''
    if p.lighting:
        try:
            cls,cmd,led,tx=(15,0x84,0,0x1F) if p.lighting=='extended' else (3,0x83,5,p.tx)
            data=query(dev,p,cls,cmd,3,(1,led),tx)
            if data[:2]!=bytes([1,led]):raise ValueError('亮度灯区不匹配')
            raw=data[2:3]
            light=Light(UNKNOWN_EFFECT,(data[2]*100+127)//255)
        except (OSError,ValueError) as exc:error=str(exc)
    return State(mouse,'Razer '+p.name,base//divisor,p.rates,0,(d.vid,d.pid,serial),light,raw,error)


def write(dev,d,current,rate,light):
    p=match(d)
    if rate!=current.rate:
        if p.extended:
            args=(0,8000//rate)
            query(dev,p,0,0x40,2,args)
            if p.commit is not None:query(dev,p,0,0x40,2,(1,args[1]),p.commit)
        else:query(dev,p,0,5,1,(1000//rate,))
        time.sleep(.2)
    if light is not None:
        if p.lighting=='extended':
            args=[1,0,light.mode]
            args+=([0,0,1,*light.rgb] if light.mode==1 else [1,0x28,0] if light.mode==4 else [0,0,0])
            query(dev,p,15,2,len(args),args,0x1F)
            query(dev,p,15,4,3,(1,0,(light.brightness*255+50)//100),0x1F)
        else:
            args={0:[0],1:[6,*light.rgb],3:[4],4:[1,1]}[light.mode]
            query(dev,p,3,10,len(args),args)
            query(dev,p,3,3,3,(1,5,(light.brightness*255+50)//100))
    # The effect cannot be queried. Only brightness and polling are verified.
    wanted=replace(current,rate=rate)
    if light is not None:
        wanted=replace(wanted,light=replace(light,mode=UNKNOWN_EFFECT,rgb=(0,0,0)),
                       raw=bytes([(light.brightness*255+50)//100]))
    return wanted
