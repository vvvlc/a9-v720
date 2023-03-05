import threading
from datetime import datetime

from log import log

from netsrv import netsrv
from netsrv_tcp import netsrv_tcp
from netsrv_udp import netsrv_udp

import cmd_udp
from prot_udp import prot_udp
from prot_json_udp import prot_json_udp


class v720_sta(log):
    TCP_PORT = 6123
    UDP_PORT = 6123
    CLI_TG = '00112233445566778899aabbccddeeff'
    CLI_TKN = 'deadc0de'

    def __init__(self, tcp_conn: netsrv_tcp, udp_conn=None) -> None:
        super().__init__('V720-STA')
        self._raw_hnd_lst = {
            f'{cmd_udp.P2P_UDP_CMD_JSON}': self.__json_hnd,
            f'{cmd_udp.P2P_UDP_CMD_HEARTBEAT}': self.__heartbeat_hnd,
        }

        self._json_hnd_lst = {
            f'{cmd_udp.CODE_2S_REGISTER_REQ}': self.__reg_req_hnd,
            f'{cmd_udp.CODE_D2S_NAT_RSP}': self.__nat_probe_hnd,
            f'{cmd_udp.CODE_C2S_UDP_REQ}': self.__udp_probe_hnd,
            f'{cmd_udp.CODE_D2C_PROBE_RSP}': self.__data_ch_probe_hnd,
            f'{cmd_udp.CODE_CMD_FORWARD}': self.__fwd_resp_hnd,
        }

        self._fwd_hnd_lst = {
            f'{cmd_udp.CODE_FORWARD_DEV_BASE_INFO}': self.__baseinfo_hnd,
            f'{cmd_udp.CODE_FORWARD_OPEN_A_OPEN_V}': self.__on_open_video,
        }

        self._uid = None
        self._data_ch_probed = False
        self.set_tcp_conn(tcp_conn)
        if udp_conn is not None:
            self.set_udp_conn(udp_conn)
        else:
            self._udp = None

    @property
    def id(self):
        return self._uid

    @property
    def host(self):
        return self._tcp._host

    def set_tcp_conn(self, tcp_conn: netsrv_tcp):
        self._tcp = tcp_conn
        self._tcpth = threading.Thread(
            target=self.__tcp_hnd, name=f'{tcp_conn}')
        self._tcpth.setDaemon(True)
        self._tcpth.start()

    def set_udp_conn(self, udp_conn: netsrv_udp):
        self._udp = udp_conn
        self._udpth = threading.Thread(
            target=self.__udp_hnd, name=f'{udp_conn}')
        self._udpth.setDaemon(True)
        self._udpth.start()

    def __tcp_hnd(self):
        while not self._tcp.is_closed:
            self.__on_tcp_rcv(self._tcp.recv())

    def __on_tcp_rcv(self, data: bytes):
        req = prot_udp.resp(data)
        self.dbg(f'Request (TCP): {req.__repr__()}')

        if f'{req.cmd}' in self._raw_hnd_lst:
            self._raw_hnd_lst[f'{req.cmd}'](self._tcp, data)
        else:
            self.warn(f'Unknown request {req}')

    def __udp_hnd(self):
        while not self._udp.is_closed:
            self.__on_udp_rcv(self._udp.recv())

    def __on_udp_rcv(self, data):
        req = prot_udp.resp(data)
        self.dbg(f'Request (UDP): {req.__repr__()}')

        if f'{req.cmd}' in self._raw_hnd_lst:
            self._raw_hnd_lst[f'{req.cmd}'](self._udp, data)
        else:
            self.warn(f'Unknown request {req}')

    def __json_hnd(self, conn: netsrv, payload: bytes):
        pkg = prot_json_udp.resp(payload)
        if f'{pkg.json["code"]}' in self._json_hnd_lst:
            self.dbg(f'Receive JSON: {pkg}')
            self._json_hnd_lst[f'{pkg.json["code"]}'](conn, pkg)
        else:
            self.warn(f'Receive unknown JSON: {pkg}')

    def __fwd_resp_hnd(self, conn: netsrv, pkg: prot_json_udp):
        cmd = pkg.json['content']['code']

        if f'{cmd}' in self._fwd_hnd_lst:
            self.dbg(f'Receive FWD: {pkg.json["content"]}')
            self._fwd_hnd_lst[f'{cmd}'](conn, pkg)
        else:
            self.warn(f'Receive unknown FWD: {pkg}')

    def __heartbeat_hnd(self, conn: netsrv, payload: bytes):
        self.info('Heartbeat received, sending heartbeat response')
        conn.send(prot_udp(cmd=cmd_udp.P2P_UDP_CMD_HEARTBEAT).req())

    def __reg_req_hnd(self, conn: netsrv_tcp, pkg: prot_json_udp):
        self._uid = pkg.json["uid"]
        self.info(f'Recieve registration request (device: {self._uid})')
        resp = prot_json_udp(json={
            'code': cmd_udp.CODE_S2_REGISTER_RSP,
            'status': 200
        })
        self.dbg(f'send registration response: {resp}')
        conn.send(resp.req())
        self.__send_nat_probe(conn)

    def __send_nat_probe(self, conn: netsrv_tcp):
        self.info(f'Sending NAT probe request')
        req = prot_json_udp(json={
            'code': cmd_udp.CODE_S2D_NAT_REQ,
            'cliTarget': self.CLI_TG,
            'cliToken': self.CLI_TKN,
            'cliIp': '255.255.255.255',  # make it unaccessible from anywhere
            'cliPort': 0,
            'cliNatIp': '255.255.255.255',
            'cliNatPort': 0
        })
        self.dbg(f'NAT probe request: {req}')
        conn.send(req.req())

    def __udp_probe_hnd(self, conn: netsrv_udp, pkg: prot_json_udp):
        self.info('Found UDP probing, sending response')
        resp = prot_json_udp(json={
            'code': cmd_udp.CODE_S2C_UDP_RSP,
            'ip': '255.255.255.255',  # make it unaccessible from anywhere
            'port': 0
        })
        self.dbg(f'UDP probing response: {resp}')
        conn.send(resp.req())
        self._data_ch_probed = False

    def __nat_probe_hnd(self, conn: netsrv_tcp, pkg: prot_json_udp):
        self.info(f'Receive NAT probation status {pkg}')

    def __data_ch_probe_hnd(self, conn: netsrv_udp, pkg: prot_json_udp):
        if not self._data_ch_probed:
            self.info(f'Device probing data channel, probe again')
            resp = prot_json_udp(json={
                'code': cmd_udp.CODE_C2D_PROBE_REQ,
            })
            self._data_ch_probed = True
            self.dbg(f'Data-channel probing response: {resp}')
            conn.send(resp.req())
        else:
            self.info(f'Device probing done')
            self.__initial_sequence()

    @staticmethod
    def __prep_fwd(content: dict) -> prot_json_udp:
        return prot_json_udp(json={
            'code': cmd_udp.CODE_CMD_FORWARD,
            'target':  v720_sta.CLI_TG,
            'content': content
        })

    def __initial_sequence(self):
        self.info(f'Sending initial sequence')
        resp = prot_json_udp(json={
            'code': cmd_udp.CODE_S2_DEVICE_STATUS,
            'status': 1
        })
        self.dbg(f'Updating device status: {resp}')
        self._tcp.send(resp.req())

        resp = self.__prep_fwd({
            'code': cmd_udp.CODE_RETRANSMISSION
        })
        self.dbg(f'Send forward-retransmission command: {resp}')
        self._tcp.send(resp.req())

        resp = self.__prep_fwd({
            'unixTimer': int(datetime.timestamp(datetime.now())),
            'code': cmd_udp.CODE_FORWARD_DEV_BASE_INFO
        })
        self.dbg(f'Send baseinfo command: {resp}')
        self._tcp.send(resp.req())

    def __baseinfo_hnd(self, conn: netsrv_tcp, pkg: prot_json_udp):
        self.info(f'Found device, starting video-streaming')

    def __on_open_video(self, conn: netsrv_tcp, pkg: prot_json_udp):
        self.info(f'Starting video streaming')


if __name__ == '__main__':
    from v720_http import v720_http

    devices = []
    _mtx = threading.Lock()

    def tcp_thread():
        with netsrv_tcp('', v720_sta.TCP_PORT) as _tcp:
            while True:
                _tcp.open()
                fork = _tcp.fork()
                if fork is not None:
                    with _mtx:
                        devices.append(v720_sta(fork))

    def udp_thread():
        with netsrv_udp('', v720_sta.UDP_PORT) as _udp:
            while True:
                _udp.open()
                fork = _udp.fork()
                if fork is not None:
                    dev_found = False
                    with _mtx:
                        for dev in devices:
                            if dev.host == fork._host:
                                dev.set_udp_conn(fork)
                                dev_found = True
                                break

                    if not dev_found:
                        fork.info('Device for connection is not found')

    http_th = threading.Thread(target=v720_http.serve_forever, name='HTTP-SRV')
    http_th.setDaemon(True)
    http_th.start()

    tcp_th = threading.Thread(target=tcp_thread, name='TCP-SRV')
    tcp_th.setDaemon(True)
    tcp_th.start()

    udp_th = threading.Thread(target=udp_thread, name='UDP-SRV')
    udp_th.setDaemon(True)
    udp_th.start()

    tcp_th.join()
