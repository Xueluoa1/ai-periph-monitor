"""Windows HID parser evidence. No settings writes or ordinary input capture."""
import ctypes
import time
import hid_win as hw


class LinkNode(ctypes.Structure):
    _fields_=[(n,ctypes.c_ushort) for n in ('LinkUsage','LinkUsagePage','Parent','NumberOfChildren','NextSibling','FirstChild')]+[
        ('Flags',ctypes.c_ulong),('UserContext',ctypes.c_void_p)]


def cap_record(c,kind,button=False):
    names=('UsagePage','ReportID','IsAlias','BitField','LinkCollection','LinkUsage','LinkUsagePage',
           'IsRange','IsStringRange','IsDesignatorRange','IsAbsolute')
    result={n:int(getattr(c,n)) for n in names}
    result['report_type']=kind
    result['control_type']='button' if button else 'value'
    if not button:
        result.update({n:int(getattr(c,n)) for n in ('HasNull','BitSize','ReportCount','UnitsExp','Units','LogicalMin','LogicalMax','PhysicalMin','PhysicalMax')})
    # The native range union contains eight USHORTs, including string/data indices.
    values=list((ctypes.c_ushort*8).from_address(ctypes.addressof(c.u)))
    fields=('UsageMin','UsageMax','StringMin','StringMax','DesignatorMin','DesignatorMax','DataIndexMin','DataIndexMax') if c.IsRange else (
        'Usage','Reserved1','StringIndex','Reserved2','DesignatorIndex','Reserved3','DataIndex','Reserved4')
    result['range' if c.IsRange else 'single']={n:v for n,v in zip(fields,values) if not n.startswith('Reserved')}
    return result


def descriptor(dev):
    caps=dev.caps
    result={'source':'Windows HidP parser','raw_report_descriptor':None,
            'raw_descriptor_status':'not_available_via_HidP; parsed capabilities are not original descriptor bytes',
            'caps':{n:int(getattr(caps,n)) for n,_ in hw.HIDP_CAPS._fields_ if n!='Reserved'},
            'controls':[],'collections':[],'errors':[]}
    for rtype,kind in ((0,'input'),(1,'output'),(2,'feature')):
        try:
            result['controls'].extend(cap_record(c,kind) for c in dev.value_caps(rtype))
            count=ctypes.c_ushort(getattr(caps,'Number'+kind.title()+'ButtonCaps'))
            if count.value:
                arr=(hw.HIDP_BUTTON_CAPS*count.value)()
                status=hw.hid.HidP_GetButtonCaps(rtype,ctypes.byref(arr),ctypes.byref(count),dev._preparsed)
                if status!=hw.HIDP_STATUS_SUCCESS:raise OSError('HidP_GetButtonCaps: '+hex(status & 0xffffffff))
                result['controls'].extend(cap_record(c,kind,True) for c in arr[:count.value])
        except (OSError,ValueError) as exc:result['errors'].append(kind+': '+str(exc))
    count=ctypes.c_ulong(caps.NumberLinkCollectionNodes)
    if count.value:
        fn=hw.hid.HidP_GetLinkCollectionNodes
        fn.argtypes=[ctypes.POINTER(LinkNode),ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
        fn.restype=ctypes.c_long
        arr=(LinkNode*count.value)()
        status=fn(arr,ctypes.byref(count),dev._preparsed)
        if status==hw.HIDP_STATUS_SUCCESS:
            result['collections']=[{**{n:int(getattr(c,n)) for n,_ in LinkNode._fields_ if n not in ('UserContext','Flags')},
                                    'CollectionType':c.Flags&255,'IsAlias':bool(c.Flags&256)} for c in arr[:count.value]]
        else:result['errors'].append('HidP_GetLinkCollectionNodes: '+hex(status & 0xffffffff))
    return result


def vendor_input_ids(evidence,iface):
    if iface.reserved or iface.usage_page<0xFF00 or not 1<=iface.input_len<=4096 or evidence['errors']:return set()
    inputs=[c for c in evidence['controls'] if c['report_type']=='input']
    # Require every declared input field to be vendor-defined, including buttons.
    if not inputs or any(c['UsagePage']<0xFF00 for c in inputs):return set()
    return {c['ReportID'] for c in inputs}


def observe(dev,iface,evidence,emit):
    ids=vendor_input_ids(evidence,iface)
    if not ids:
        emit('passive_status',status='skipped',reason='No exclusively vendor-defined input collection')
        return
    seen=set();total=0;deadline=time.monotonic()+1.5
    # Do not drain: the first queued status report may be the only battery evidence.
    # Bypass the wrapper trace until report ID is checked.
    while total<32 and len(seen)<8 and time.monotonic()<deadline:
        raw=hw.HidDevice.read_input(dev,timeout_ms=100,length=iface.input_len)
        if not raw:continue
        total+=1
        if raw[0] not in ids or raw in seen:continue
        seen.add(raw)
        emit('rx',brand='vendor_passive',label='unsolicited_status',hex=raw.hex(),decoded=False)
    emit('passive_status',status='captured' if seen else 'no_report',unique_reports=len(seen),
         interpretation='Raw vendor data only; no inferred battery percentage')


def query_status(events,summary,worker_status):
    if worker_status not in ('completed','inventory_only'):return worker_status
    if summary.get('capabilities',{}).get('battery') is not None:return 'battery_candidate'
    tx=any(e.get('event') in ('tx','transport_tx') for e in events)
    rx=any(e.get('event') in ('rx','transport_rx') for e in events)
    if tx:return 'query_response_unparsed' if rx else 'query_no_response'
    if rx:return 'raw_reports_only'
    if summary.get('probes_executed',[])==['generic_safe_dump']:return 'protocol_not_registered'
    return 'not_queried'
