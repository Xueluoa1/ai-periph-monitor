"""Failure retention, IPC snapshot sharing, and Shell icon recovery regressions."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import battery as b
import shared_readings as s
import tray_app as tray
import tray_common as tc
import tray_pixels as pixels
import tray_font


def reading(level=65, key='373B:101A:FF02', **kw):
    return b.DiscoveredReading(key=key, title='Test mouse', subtitle='test', level=level, **kw)


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = patch.object(tc, 'DATA_DIR', self.tmp.name)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.tmp.cleanup()

    def sample(self, level=65, now=100):
        return s.merge_snapshot(s.empty_snapshot(), [reading(level)], now)

    def test_timeout_and_zero_never_erase_connected_value(self):
        for level in (0, 50, 100):
            first = self.sample(level)
            for incoming in ([reading(None,error='timeout')],):
                failed = s.merge_snapshot(first,incoming,200)
                result = s.readings_from(failed)[0]
                self.assertEqual((result.level,result.updated_at,result.stale),(level,100,True))
                self.assertIn('上次电量',result.status)
                self.assertTrue(result.error)

    def test_recovery_updates_value_timestamp_and_status(self):
        failed = s.merge_snapshot(self.sample(),[reading(None)],200)
        recovered = s.merge_snapshot(failed,[reading(64,status='放电中')],300)
        r = s.readings_from(recovered)[0]
        self.assertEqual((r.level,r.updated_at,r.stale,r.status),(64,300,False,'放电中'))
        self.assertEqual(r.error,'')

    def test_one_failed_device_does_not_block_other_updates(self):
        first=s.merge_snapshot(self.sample(),[reading(),reading(80,key='BT:headset')],200)
        current=s.merge_snapshot(first,[reading(None),reading(79,key='BT:headset')],300)
        rows={r.key:r for r in s.readings_from(current)}
        self.assertEqual(rows['373B:101A:FF02'].level,65)
        self.assertTrue(rows['373B:101A:FF02'].stale)
        self.assertEqual(rows['BT:headset'].level,79)
        self.assertFalse(rows['BT:headset'].stale)

    def test_sleep_does_not_refresh_timestamp_or_fabricate_initial_value(self):
        result=s.readings_from(s.merge_snapshot(self.sample(),[reading(65,status='休眠中')],200))[0]
        self.assertEqual((result.updated_at,result.stale),(100,True))
        unknown=s.readings_from(s.merge_snapshot(s.empty_snapshot(),[reading(None)],200))[0]
        self.assertIsNone(unknown.level)

    def test_repeated_failure_does_not_accumulate_status_labels(self):
        data=self.sample()
        for t in range(200,210): data=s.merge_snapshot(data,[reading(None)],t)
        self.assertEqual(s.readings_from(data)[0].status.count('上次电量'),1)

    def test_disconnected_devices_are_removed_and_do_not_return_after_restart(self):
        first=s.merge_snapshot(self.sample(),[reading(),reading(80,key='BT:headset')],200)
        removed=s.merge_snapshot(first,[],300)
        self.assertEqual(s.readings_from(removed),[])
        s.save_snapshot(removed)
        s._memory.pop(str(s.snapshot_path()),None)
        self.assertEqual(s.readings_from(s.load_snapshot()),[])

    def test_only_missing_device_removed_and_reconnection_is_fresh(self):
        first=s.merge_snapshot(self.sample(),[reading(),reading(80,key='BT:headset')],200)
        removed=s.merge_snapshot(first,[reading(79,key='BT:headset')],300)
        self.assertEqual([r.key for r in s.readings_from(removed)],['BT:headset'])
        reconnected=s.merge_snapshot(removed,[reading(45),reading(79,key='BT:headset')],400)
        r=next(r for r in s.readings_from(reconnected) if not r.key.startswith('BT:'))
        self.assertEqual((r.level,r.updated_at,r.stale),(45,400,False))

    def test_present_hid_without_battery_report_retains_value(self):
        for presence in ({'hid_keys':{reading().key}}, {'hid_unknown':{'373B:101A'}}):
            result=s.readings_from(s.merge_snapshot(self.sample(),[],200,presence=presence))
            self.assertEqual((result[0].level,result[0].updated_at,result[0].stale),(65,100,True))

    def test_bluetooth_failure_keeps_bt_but_still_removes_unplugged_usb(self):
        first=s.merge_snapshot(self.sample(),[reading(),reading(80,key='BT:headset')],200)
        result=s.readings_from(s.merge_snapshot(first,[],300,presence={'bt_error':'BT enumeration failed'}))
        self.assertEqual([r.key for r in result],['BT:headset'])
        self.assertTrue(result[0].stale)
        self.assertIn('BT enumeration failed',result[0].error)

    def test_discovery_passes_connection_inventory_to_collector(self):
        import bt_battery as bb
        from types import SimpleNamespace
        s.save_snapshot(self.sample())
        iface=SimpleNamespace(vid=0x373B,pid=0x101A,usage_page=0xFF02,feature_len=0)
        with patch.object(b.hw,'enumerate_hid',return_value=[iface]), \
             patch.object(b.peripheral,'prune_listeners'), \
             patch.object(b,'_is_candidate_interface',return_value=False), \
             patch.object(b,'_load_custom_models',return_value={}), \
             patch.object(bb,'connected_devices',return_value=[]):
            self.assertEqual(s.readings_from(s.collect(force=True))[0].level,65)
        with patch.object(b.hw,'enumerate_hid',return_value=[]), \
             patch.object(b.peripheral,'prune_listeners'), \
             patch.object(b,'_load_custom_models',return_value={}), \
             patch.object(bb,'connected_devices',return_value=[]):
            self.assertEqual(s.readings_from(s.collect(force=True)),[])

    def test_bluetooth_unknown_connection_is_retained_but_disconnected_is_removed(self):
        import bt_battery as bb
        first=s.merge_snapshot(s.empty_snapshot(),[reading(80,key='BT:headset')],100)
        for connected in (None,False,True):
            presence={}
            dev=bb.BluetoothDevice('Headset','headset',connected=connected)
            with patch.object(bb,'list_bluetooth_devices',return_value=[dev]):
                discovered=bb.connected_devices(presence=presence)
            incoming=[reading(None,key='BT:'+d.instance_id) for d in discovered]
            result=s.readings_from(s.merge_snapshot(first,incoming,200,presence=presence))
            self.assertEqual(len(result),0 if connected is False else 1)
            if result:self.assertEqual(result[0].level,80)

    def test_bt_enumeration_exception_is_not_reported_as_disconnect(self):
        import bt_battery as bb
        first=s.merge_snapshot(s.empty_snapshot(),[reading(80,key='BT:headset')],100)
        s.save_snapshot(first)
        with patch.object(b.hw,'enumerate_hid',return_value=[]), \
             patch.object(b,'_load_custom_models',return_value={}), \
             patch.object(bb,'connected_devices',side_effect=OSError('enumeration failed')):
            result=s.readings_from(s.collect(force=True))
        self.assertEqual(result[0].level,80)
        self.assertTrue(result[0].stale)

    def test_windows_enumeration_failure_raises_instead_of_empty_success(self):
        import bt_battery as bb
        with patch.object(b.hw.setupapi,'SetupDiGetClassDevsW',return_value=1), \
             patch.object(b.hw.setupapi,'SetupDiEnumDeviceInterfaces',return_value=False), \
             patch.object(b.hw.setupapi,'SetupDiEnumDeviceInfo',return_value=False), \
             patch.object(b.hw.setupapi,'SetupDiDestroyDeviceInfoList'), \
             patch.object(b.hw.ctypes,'get_last_error',return_value=5):
            with self.assertRaises(OSError):b.hw.enumerate_hid()
            with self.assertRaises(OSError):bb.list_bluetooth_devices()

    def test_atomic_persistence_and_corruption_preserve_memory(self):
        data=self.sample()
        s.save_snapshot(data)
        self.assertEqual(s.load_snapshot(),data)
        s.snapshot_path().write_text('{broken',encoding='utf8')
        self.assertEqual(s.load_snapshot(),data)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')),[])

    def test_disk_write_failure_does_not_restore_older_value(self):
        s.save_snapshot(self.sample(65,100))
        newer=self.sample(64,200)
        with patch.object(s.os,'replace',side_effect=OSError()):
            with self.assertRaises(OSError): s.save_snapshot(newer)
        self.assertEqual(s.readings_from(s.load_snapshot())[0].level,64)

    def test_windows_reader_contention_retries_atomic_replace(self):
        original = s.os.replace
        attempts = []
        def replace_file(source, destination):
            attempts.append(source)
            if len(attempts) < 3:
                raise PermissionError('File temporarily held by reader')
            return original(source, destination)
        data = self.sample()
        with patch.object(s.os, 'replace', side_effect=replace_file), patch.object(s.time, 'sleep'):
            s.save_snapshot(data)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(s.snapshot_path().read_text(encoding='utf8')), data)
        self.assertFalse(list(Path(self.tmp.name).glob('*.tmp')))

    def test_scheduled_consumers_share_one_acquisition(self):
        with patch.object(b,'discover_and_read_all',return_value=[reading()]) as scan:
            a=s.collect(); c=s.collect()
            self.assertEqual(a['revision'],c['revision'])
            self.assertEqual(scan.call_count,1)
            fresh=s.collect(force=True)
            self.assertEqual(scan.call_count,2)
            self.assertNotEqual(a['revision'],fresh['revision'])

    def test_scan_exception_retains_every_existing_icon(self):
        s.save_snapshot(self.sample())
        with patch.object(b,'discover_and_read_all',side_effect=OSError('test scan failure')):
            result=s.readings_from(s.collect(force=True))[0]
        self.assertEqual(result.level,65)
        self.assertTrue(result.stale)
        self.assertIn('test scan failure',result.error)

    def test_next_scan_is_due_from_completion_not_each_window_timer(self):
        data=self.sample(now=100)
        with patch.object(tc,'load_config',return_value={'interval':60}),patch.object(s.time,'time',return_value=159.9):
            self.assertFalse(s.due(data))
        with patch.object(tc,'load_config',return_value={'interval':60}),patch.object(s.time,'time',return_value=160):
            self.assertTrue(s.due(data))

    def test_process_restart_can_read_saved_percentage(self):
        s.save_snapshot(self.sample())
        code='import sys; import tray_common as tc, shared_readings as s; tc.DATA_DIR=sys.argv[1]; r=s.readings_from(s.load_snapshot())[0]; print(r.level)'
        result=subprocess.run([sys.executable,'-c',code,self.tmp.name],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'65')

    def test_cross_process_lock_coalesces_simultaneous_refresh(self):
        code='''import sys,time
from pathlib import Path
import shared_readings as s, tray_common as tc, battery as b
tc.DATA_DIR=sys.argv[1]
def scan(**kw):
    Path(tc.DATA_DIR,'started').write_text('1')
    time.sleep(1)
    return [b.DiscoveredReading(key='device',title='Mouse',subtitle='',level=82)]
b.discover_and_read_all=scan
s.collect(force=True)
'''
        child=subprocess.Popen([sys.executable,'-c',code,self.tmp.name],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            until=time.monotonic()+5
            while not Path(self.tmp.name,'started').exists() and time.monotonic()<until:
                time.sleep(.02)
            self.assertTrue(Path(self.tmp.name,'started').exists())
            with patch.object(b,'discover_and_read_all',side_effect=AssertionError('duplicate scan')):
                s.collect(force=True)
            _,error=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0,error)
            self.assertEqual(s.readings_from(s.load_snapshot())[0].level,82)
        finally:
            if child.poll() is None: child.kill();child.wait()

    def test_main_window_uses_shared_result(self):
        import my_devices
        with patch.object(s,'collect',return_value=self.sample()) as collect, patch.object(b,'discover_and_read_all',side_effect=AssertionError('direct scan')):
            result=my_devices.read_all(force=True)
        collect.assert_called_once_with(force=True)
        self.assertEqual(result[0].level,65)


class TrayTests(unittest.TestCase):
    def app(self):
        with patch.object(s,'load_snapshot',return_value=s.empty_snapshot()),patch.object(tc,'load_config',return_value=dict(tc.DEFAULT_CONFIG)):
            app=tray.TrayApp()
        app.readings=[reading()];app.hwnd=1
        return app

    def test_shell_modify_failure_readds_without_deleting(self):
        app=self.app();uid=app._current_slots()[0][0];app.active_uids={uid}
        with patch.object(app,'_apply_icon',side_effect=[False,True]) as icon:
            app.update_icons()
        self.assertEqual([c.args[0] for c in icon.call_args_list],[tray.NIM_MODIFY,tray.NIM_ADD])
        self.assertEqual(app.active_uids,{uid})

    def test_failed_add_is_retried_and_selection_still_respected(self):
        app=self.app()
        with patch.object(app,'_apply_icon',return_value=False):app.update_icons()
        self.assertTrue(app.icon_retry)
        with patch.object(app,'_apply_icon',return_value=True),patch.object(tc,'promote_tray_icons_to_taskbar'):app.update_icons()
        self.assertFalse(app.icon_retry)
        app.cfg['taskbar_devices']=[]
        self.assertEqual(app._current_slots(),[])

    def test_external_snapshot_updates_tray_without_hardware_scan(self):
        app=self.app()
        snapshot=s.merge_snapshot(s.empty_snapshot(),[reading(48)],100)
        with patch.object(s,'load_snapshot',return_value=snapshot),patch.object(app,'update_icons') as update,patch.object(app,'check_low_battery_alerts'),patch.object(b,'discover_and_read_all',side_effect=AssertionError()):
            app.adopt_snapshot();app.adopt_snapshot()
        self.assertEqual(app.readings[0].level,48)
        update.assert_called_once()

    def test_retained_low_battery_does_not_send_new_alert(self):
        app=self.app();app.readings=[reading(5,stale=True)]
        with patch.object(app,'send_notification') as notify:app.check_low_battery_alerts()
        notify.assert_not_called()

    def test_disconnect_removes_device_icon_when_snapshot_arrives(self):
        app=self.app();uid=app._current_slots()[0][0];app.active_uids={uid}
        snapshot=s.merge_snapshot(s.empty_snapshot(),[],100)
        with patch.object(s,'load_snapshot',return_value=snapshot), \
             patch.object(tray.SHELL32,'Shell_NotifyIconW',return_value=True) as shell, \
             patch.object(app,'_apply_icon',return_value=True), \
             patch.object(tc,'promote_tray_icons_to_taskbar'):
            app.adopt_snapshot()
        self.assertEqual(app.readings,[])
        self.assertNotIn(uid,app.active_uids)
        self.assertIn(tray.NIM_DELETE,[call.args[0] for call in shell.call_args_list])

    def test_null_hicon_never_overwrites_existing_icon(self):
        app=self.app()
        with patch.object(tray.win_icon,'make_hicon',return_value=0),patch.object(tray.SHELL32,'Shell_NotifyIconW') as shell:
            self.assertFalse(app._apply_icon(tray.NIM_MODIFY,2,'Mouse',65,'',False,'usb'))
        shell.assert_not_called()


class IconTests(unittest.TestCase):
    def test_every_number_has_clear_space_from_all_four_borders(self):
        for size in (16,20,24,32,40,48,64):
            margin=max(1,round(size/32))+max(2,round(size*.08))+max(1,round(size/16))
            for value in list(map(str,range(101)))+['--']:
                mask=tray_font.digit_mask(value,size)
                points=[(i%size,i//size) for i,a in enumerate(mask) if a]
                self.assertTrue(points,(size,value))
                self.assertTrue(all(margin<=x<size-margin and margin<=y<size-margin for x,y in points),(size,value))

    def test_embedded_font_renders_all_numbers_at_taskbar_sizes(self):
        self.assertTrue(tray_font.FONT_PATH.is_file())
        for size in (16,20,24,32,48):
            for n in (0,1,8,10,11,50,99,100):
                mask=tray_font.digit_mask(str(n),size)
                self.assertTrue(any(mask),(n,size))
                self.assertEqual(len(mask),size*size)

    def test_frame_follows_percentage_and_transport(self):
        size=32;inset=1
        for light in (True,False):
            for transport in ('usb','bluetooth'):
                for value in (0,10,50,55,60,100):
                    raw=pixels.render(str(value),size,light=light,transport=transport)
                    color=pixels.number_color(100,transport,light)[:3]
                    pale=tuple(round(v+(255-v)*.74) for v in color)
                    def pixel(x,y):return tuple(raw[(y*size+x)*4:(y*size+x)*4+4])
                    self.assertEqual(pixel(inset,1),(*(color if value >= 50 else pale),255))
                    self.assertEqual(pixel(30,1),(*(color if value==100 else pale),255))
                    self.assertEqual(pixel(2,2)[3],255) # thicker than the old one-pixel rim
                    self.assertEqual(pixel(0,0)[3],0)
                    if value==50:
                        self.assertEqual(pixel(15,1),(*color,255))
                        self.assertEqual(pixel(16,1),(*pale,255))
                    if value in (55,60):
                        self.assertEqual(pixel(2,16),(*color,255))
                        self.assertEqual(pixel(29,16),(*pale,255))
                        self.assertEqual(pixel(10,29),(*color,255))
                        self.assertEqual(pixel(21,29),(*pale,255))

    def test_perimeter_fraction_and_low_battery_text_only(self):
        points=[(16,30),(2,30),(2,16),(2,2),(16,2),(30,2),(30,16),(30,30)]
        self.assertEqual([pixels.rim_progress(x,y,16) for x,y in points],
                         [0,.125,.25,.375,.5,.625,.75,.875])
        for transport in ('usb','bluetooth'):
            for light in (True,False):
                raw=pixels.render('8',32,transport=transport,light=light)
                numeral=pixels.number_color(8,transport,light)[:3]
                border=pixels.number_color(100,transport,light)[:3]
                def pixel(x,y):return tuple(raw[(y*32+x)*4:(y*32+x)*4+3])
                self.assertEqual(pixel(10,29),border)
                self.assertNotEqual(numeral,border)
                self.assertTrue(any(pixel(x,y)==numeral for y in range(5,27) for x in range(5,27)))


if __name__=='__main__':unittest.main()
