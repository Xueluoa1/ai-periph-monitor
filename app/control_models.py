"""Exact USB identities and protocol capabilities; no vendor-wide writes.

Protocol facts: OpenMouse mouse-protocol src/lamzu, wlmouse, mchose.
See 适配说明-v28.md for provenance and hardware validation limits.
"""
from dataclasses import dataclass
import razer_controls as rz
import vendor_controls as vc

R1 = (125, 250, 500, 1000)
R8 = (125, 250, 500, 1000, 2000, 4000, 8000)
RH = (500, 1000, 2000, 4000, 8000)
RM = (125, 500, 1000, 2000, 4000, 8000)


@dataclass(frozen=True)
class Model:
    name: str
    family: str
    rates: tuple = ()
    target: int = 2
    lighting: str = ''


MODELS = {}


def _page(vid, pid, name, rates, target=2, lighting=''):
    MODELS[vid, pid] = Model(name, 'page', rates, target, lighting)


for pid, rates in ((0x1C,R1),(0x1D,R1),(0x1E,RH)):
    _page(0x373E,pid,'LAMZU Maya X',rates)
for name, ids in (('Inca 8K',(9,15,16)),('Paro Aurora',(7,13,14))):
    for pid,rates in zip(ids,(R1,R1,RH)):
        _page(0x37B0,pid,'LAMZU '+name,rates)
for name, ids in (('R5 Ultra',(0x46,0x47)),('R6',(0x21,0x22)),('R8',(0x3A,0x3B))):
    for pid,rates in zip(ids,(R1,R8)):
        _page(0x373E,pid,'Attack Shark '+name,rates,lighting='toggle' if pid==0x47 else '')
for pid,target in ((0x6A,0),(0x6B,2)):
    _page(0x373E,pid,'CRDRAKO KO-ONE',R8,target)
for pid in (0x98,0x99):
    _page(0x373E,pid,'RAWM Leviathan V4 GT',R8)
for pid,rates in ((0x66,(500,1000)),(0x67,RH)):
    _page(0x373E,pid,'Vancer Akitsu Medium 3950',rates)
for pid,name,rates,target in ((0x32,'LUNA33',R1,0),(0x33,'LUNA33',R8,2),
                              (0x54,'TYPE33',R8,0),(0x84,'TYPE33',R8,2)):
    _page(0x373E,pid,'LunaFury '+name,rates,target,'luna' if pid in (0x33,0x84) else '')
for name,ids in (('Beast G',(0xA860,0xA861)),('Huan',(0xA863,0xA864)),
                 ('Beast Miao',(0xA866,0xA867)),('Beast Mini Pro',(0xA868,0xA869)),
                 ('Beast X Pro',(0xA870,0xA871)),('Strider',(0xA872,0xA873)),
                 ('Ying',(0xA874,0xA875)),('Sword X',(0xA878,0xA879)),
                 ('Beast Max',(0xA880,0xA881)),('Beast X',(0xA883,0xA884)),
                 ('Beast Mini',(0xA885,0xA886))):
    for pid in ids:
        _page(0x36A7,pid,'WLmouse '+name,R8)
# The shared 1K receiver requires a paired-product read before enabling writes.
_page(0x36A7,0xA882,'WLmouse 1K 接收器',R1)

MCHOSE_MICE = {0x4018:'A7 V2 Pro',0x4023:'A7 V2 Pro+',0x4019:'A7 V2 Ultra',0x4021:'A7 V2 Ultra+'}
for pid,name in MCHOSE_MICE.items():
    MODELS[0x3837,pid] = Model('MCHOSE '+name,'mchose',RM)
for pid in (0x100B,0x1020):
    MODELS[0x3837,pid] = Model('MCHOSE A7 V2 接收器','mchose',RM)
MODELS[0x3837,0x1012] = Model('MCHOSE MagDock 充电底座','dock',lighting='dock')
for pid,p in rz.PRODUCTS.items():
    MODELS[0x1532,pid] = Model('Razer '+p.name,'razer',p.rates,lighting=p.lighting)
for pid in (0x522C,0x622C):
    MODELS[0x093A,pid]=Model('Incott 鼠标','incott',R8 if pid==0x522C else R1,lighting='incott' if pid==0x522C else '')
for pid in (*vc.VAXEE_NAMES,*vc.VAXEE_RECEIVERS):
    MODELS[0x3057,pid]=Model('VAXEE '+vc.VAXEE_NAMES.get(pid,'接收器'),'vaxee',
                           (500,1000,2000,4000) if pid in (0x1002,0x2001,0x2002) else (500,1000))
for pid in (0x1117,0x1114):
    MODELS[0x260D,pid]=Model('Dareu A950 PRO Mg','dareu',R1 if pid==0x1117 else (*R1,2000,4000),lighting='dareu')


def match(iface):
    model = MODELS.get((iface.vid,iface.pid))
    if model is None:return None
    if model.family=='razer':return model if rz.match(iface) else None
    if model.family=='incott':
        return model if iface.usage_page==0xFF05 and 9<=iface.feature_len<=64 else None
    if model.family=='vaxee':
        return model if (iface.usage_page,iface.usage,iface.feature_len)==(0xFF05,1,64) else None
    if model.family=='dareu':
        return model if (iface.usage_page,iface.usage,iface.output_len,iface.input_len)==(0xFF02,2,17,17) else None
    if model.family=='page':
        valid = iface.usage_page==0xFFFF and iface.feature_len==65
    elif model.family=='mchose':
        valid = (iface.usage_page,iface.usage)==(0xFF01,1) and iface.feature_len==65
    else:
        valid = (iface.usage_page,iface.usage,iface.output_len,iface.input_len)==(0xFF00,1,64,64)
    return model if valid else None
