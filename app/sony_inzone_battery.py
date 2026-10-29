"""Battery GET only for INZONE H5/H9 II; published HeadsetControl wire facts."""
import secrets


def request():
    # Avoid unsolicited transaction 1 and vary each session to reject old replies.
    tid = secrets.randbelow(65534) + 2
    packet = bytearray(64)
    packet[:13] = bytes.fromhex('020c0100fc0896c3410401') + tid.to_bytes(2, 'little')
    packet[13] = sum(packet[6:13]) & 255
    return bytes(packet), tid


def decode(raw, tid):
    r = bytes(raw or b'')
    if len(r) != 64 or r[0] != 2 or not 14 <= r[1] <= 62:
        return None
    end = r[1]
    if (r[2:4] != b'\x04\xff' or r[5:8] != b'\0\x96\xc3'
            or r[8] != 0x14 or r[9:11] != b'\x04\x10'
            or int.from_bytes(r[11:13], 'little') != tid
            or (sum(r[5:end+1]) & 255) != r[end+1]):
        return None
    if r[14] > 100:
        return None
    return r[14], 'charging' if r[13] else 'discharging'
