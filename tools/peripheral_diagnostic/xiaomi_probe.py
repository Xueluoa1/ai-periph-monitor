"""Query-only gate for the Xiaomi M002 public-driver protocol."""
import xiaomi_mouse2 as xm


class QueryDevice:
    def __init__(self,dev,emit):self.dev,self.emit=dev,emit

    def set_feature_report(self,raw):
        sizes={0x16:0,0x80:0,0x2B:1,0x2D:2,0x25:1}
        if len(raw)!=64 or raw[0]!=5 or raw[1] not in (0,1) or raw[2] not in sizes:
            raise ValueError('诊断器拒绝小米设置命令')
        size=sizes[raw[2]]
        if raw[3]!=size or any(raw[4+size:]):raise ValueError('诊断器拒绝未知小米查询内容')
        if size and not 1<=raw[4]<=5:raise ValueError('配置层无效')
        if size==2 and raw[5]>4:raise ValueError('档位无效')
        self.emit('tx',brand='xiaomi_mouse2',method='feature_query',hex=raw.hex())
        self.dev.set_feature_report(raw)

    def get_feature_report(self,rid,length):
        raw=self.dev.get_feature_report(rid,length)
        self.emit('rx',brand='xiaomi_mouse2',method='feature',hex=raw.hex())
        return raw


def probe(dev,iface,emit):
    if not xm.matches(iface):return {}
    query=QueryDevice(dev,emit);result={}
    try:
        level,status=xm.battery(query,iface)
        result['battery']={'level_percent':level,'charging':status=='charging'}
        emit('decoded',brand='xiaomi_mouse2',label='battery',value=result['battery'])
    except (OSError,ValueError) as exc:
        emit('query_error',brand='xiaomi_mouse2',label='battery',message=str(exc))
    try:
        config=xm.read_config(query,iface)
        result['dpi']={'profile':config.profile,'count':config.count,'active_stage':config.active+1,
            'rows':[dict(stage=r[0]+1,x=int.from_bytes(r[1:3],'little'),
                         y=int.from_bytes(r[7:9],'little') if r[6] else int.from_bytes(r[1:3],'little'),
                         independent_y=bool(r[6]),color='#'+r[3:6].hex(),raw=r.hex()) for r in config.rows]}
        result['polling_rate']={'hz':config.rate,'mode':config.rate_data[5],'motion_sync':config.rate_data[6]}
        result['lighting']={'kind':'dpi_stage_indicators','current_effect':'unconfirmed',
                            'colors':['#'+r[3:6].hex() for r in config.rows]}
        emit('decoded',brand='xiaomi_mouse2',label='configuration',value=result)
    except (OSError,ValueError) as exc:
        emit('query_error',brand='xiaomi_mouse2',label='configuration',message=str(exc))
    return result
