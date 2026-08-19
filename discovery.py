#!/usr/bin/env python3

import json
import logging
import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from time import perf_counter

init_timer = perf_counter()

REDE = ""
LOCALHOST = ""

TELEGRAF_CONF = "/etc/telegraf/telegraf.d/ping.conf"
PROMETHEUS_FILE = "/etc/prometheus/targets/rede.json"
INVENTORY_FILE = "inventory.json"
MAC_DICT_FILE = "mac_dictionary.json"
HISTORY_FILE = "history.json"
LOG_FILE = "discovery.log"
VENDOR_FILE = "vendors_dictionary.json"
TYPE_FILE = "type_dictionary.json"
CONFIG_FILE = "config.json"
SECTION = "linux"

handler = RotatingFileHandler(
    LOG_FILE, 
    maxBytes=10*1024*1024, 
    backupCount=5, 
    encoding='utf-8'
)

logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

#
# Dicionário MAC -> Nome amigável
#
logging.info("Carregando o dicionário de MACs")

try:
    with open(MAC_DICT_FILE, "r") as f:
        MAC_DICT = {
            k.upper(): v
            for k, v in json.load(f).items()
        }

    logging.info(
        "Dicionário MAC carregado com %s entradas",
        len(MAC_DICT)
    )
        
except Exception as e:
    logging.warning("Erro ao carregar dicionário MAC: %s", e)  
    MAC_DICT = {}

logging.info("Dicionário de MACs carregado")

#
# Histórico
#
logging.info("Carregando o histórico de hosts")

try:
    with open(HISTORY_FILE, "r") as f:
        HISTORY = json.load(f)

    logging.info(
        "Histórico de hosts carregado com %s entradas",
        len(HISTORY)
    )

except Exception as e:
    logging.warning("Erro ao carregar histórico de hosts: %s", e)  
    HISTORY = {}

logging.info("Histórico de hosts carregado")

#
# Dicionário de fabricantes
#
try:
    with open(VENDOR_FILE, "r") as f:
        VENDOR_DICT = {
            k.upper(): v
            for k, v in json.load(f).items()
        }

    logging.info(
        "Dicionário de fabricantes carregado com %s entradas",
        len(VENDOR_DICT)
    )
except Exception as e:
    logging.warning("Erro ao carregar dicionário de fabricantes: %s", e)  
    VENDOR_DICT = {}    

#
# Dicionário de tipos
#
try:
    with open(TYPE_FILE, "r") as f:
        TYPE_DICT = {
            k.upper(): v
            for k, v in json.load(f).items()
        }

    logging.info(
        "Dicionário de tipos carregado com %s entradas",
        len(TYPE_DICT)
    )
except Exception as e:
    logging.warning("Erro ao carregar dicionário de tipos: %s", e)  
    TYPE_DICT = {}    

logging.info("Iniciando descoberta da rede")

def run(cmd, timeout=15):
    try:
        return {
        "success": True,
        "output": subprocess.check_output(
                cmd,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=timeout
            ),
        "error": None
        }
    except subprocess.TimeoutExpired:
        logging.warning(
            "Timeout: %s",
            " ".join(cmd)
        )
        return {
            "success": False,
            "output": "",
            "error": "Timeout expired"
        }

    except Exception as e:
        logging.warning(
            "%s -> %s",
            " ".join(cmd),
            e
        )
        return {
            "success": False,
            "output": "",
            "error": str(e)
        }

#
# Carregando o config file
#

try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
        if config:
            config_info = config[SECTION]
            REDE = config_info["network"]
            LOCALHOST = config_info["localhost"]
            logging.info(f"Loaded {len(config)} records from {CONFIG_FILE}.")
        else:
            logging.error(f"Could not read config records from {CONFIG_FILE}.")
except FileNotFoundError:
    logging.error(f"Input file {CONFIG_FILE} not found.")
except json.JSONDecodeError as e:
    logging.error(f"Error decoding JSON from {CONFIG_FILE}: {e}")


#
# Descoberta inicial
#
saida_nmap = run([
    "nmap",
    "-sn",
    "-R",
    REDE
], timeout=120)

hosts = []

for bloco in saida_nmap["output"].split("Nmap scan report for ")[1:]:

    linha = bloco.splitlines()[0]

    m = re.match(
        r"(.+)\s+\((\d+\.\d+\.\d+\.\d+)\)",
        linha
    )

    if m:
        hosts.append({
            "ip": m.group(2),
            "hostname": m.group(1)
        })
    else:
        hosts.append({
            "ip": linha.strip(),
            "hostname": None
        })

logging.info(
    "Encontrados %s hosts",
    len(hosts)
)

#
# arp-scan apenas 3 vezes para evitar problemas de timeout e perda de pacotes. O resultado é armazenado em arp_data, que é um dicionário com IPs como chaves e informações de MAC e fabricante como valores.
#
arp_data = {}

for counter in range(3):
    arp = run([
        "arp-scan",
        "--localnet"
    ])

    scan_data = {}

    if arp["success"]:
        for linha in arp["output"].splitlines():
            campos = linha.split("\t")
            if len(campos) >= 2:
                ip = campos[0].strip()
                mac = campos[1].strip()
                vendor = campos[2].strip() if len(campos) >= 3 else ""
                scan_data[ip] = {
                    "mac": mac,
                    "vendor": vendor
                }

        arp_data.update(scan_data)

        logging.info(
            "%s - Encontrados %s MACs via arp-scan",
            counter + 1,
            len(arp_data)
        )
    else:
        if arp["error"] == "Timeout expired":
            continue
        else:
            break

logging.info(
    "Encontrados %s MACs via arp-scan",
    len(arp_data)
)


def get_mac(ip):

    counter = 0
    while counter < 5:

        arp = run([
            "arp-scan",
            ip
        ], timeout=10)

        if arp["success"]:
            for linha in arp["output"].splitlines():
                campos = linha.split("\t")
                if len(campos) >= 3 and campos[1] != None:
                    logging.info(
                        "MAC encontrado para %s: %s em %d tentativas!",
                        ip,
                        campos[1],
                        counter
                    )   
                    return campos[1]
            counter += 1
        else:
            if arp["error"] == "Timeout expired":
                counter += 1
                continue
            else:
                logging.error("Não foi possível obter MAC para %s - erro: %s", ip, arp["error"])
                break

    logging.warning(
        "Não foi possível obter MAC para %s em %d tentativas",
        ip,
        counter
    )
    return None


def get_dns(ip):

    try:
        return socket.gethostbyaddr(ip)[0]
    except:
        return None


def get_mdns(ip):

    try:

        saida = run([
            "avahi-resolve-address",
            ip
        ]).strip()

        if saida["success"]:
            if "\t" in saida["output"]:
                return saida["output"].split("\t")[1]

    except:
        pass

    return None

def get_latency(ip):

    ping = run([
        "ping",
        "-c",
        "1",
        "-W",
        "1",
        ip
    ])

    if ping["success"]:
        m = re.search(
            r"time=([\d\.]+)",
            ping["output"]
        )
        return {
            "success": True,
            "latency": m.group(1) if m else None,
            "error": None
        }
    else:
        m = re.search(r"icmp_seq=\d+\s+(.*)$", ping["output"], re.MULTILINE)
        return {
            "success": False,
            "latency": "",
            "error": m.group(1) if m else ping["error"]
        }

def quiet_ping(ip):

    ping = run([
        "ping",
        "-c",
        "1",
        "-W",
        "10",
        ip
    ])

    if ping["success"]:
        m = re.search(
            r"time=([\d\.]+)",
            ping["output"]
        )
        return {
            "success": True,
            "text": m.group(1) if m else None
        }
    else:
        m = re.search(r"icmp_seq=\d+\s+(.*)$", ping["output"], re.MULTILINE)
        return {
            "success": False,
            "text": m.group(1) if m else ping["error"]
        }

def get_os(ip):

    for counter in range(6):
        resultado = run([
            "nmap",
            "-O",
            "--osscan-guess",
            "--host-timeout",
            "30s",
            ip
        ], timeout=35)

        m = re.search(
            r"Running:\s*(.+)",
            resultado["output"]
        )

        if m == None:
            m = re.search(
                r"V=([^%]+)", 
                resultado["output"]
            )

        if m:
            return m.group(1)

    logging.warning(
        "Não foi possível identificar o sistema operacional de %s após %d tentativas",
        ip,
        counter + 1
    )
    return ""

def get_type(ip):

    for counter in range(6):
        resultado = run([
            "nmap",
            "-O",
            "--osscan-guess",
            "--host-timeout",
            "30s",
            ip
        ], timeout=35)

        m = re.search(
            r"Device type:\s*(.+)",
            resultado["output"]
        )

        if m:
            return m.group(1)

    logging.warning(
        "Não foi possível identificar o tipo de dispositivo de %s após %d tentativas",
        ip,
        counter + 1
    )
    return ""

def get_open_ports(ip):

    resultado = run([
        "nmap",
        "-Pn",
        "--host-timeout",
        "20s",
        "--top-ports",
        "30",
        ip
    ], timeout=35)

    return re.findall(
        r"(\d+)/tcp\s+open",
        resultado["output"]
    )

def get_services(ip):

    resultado = run([
        "nmap",
        "-sV",
        "--host-timeout",
        "30s",
        "--top-ports",
        "20",
        ip
    ],  timeout=25)

    servicos = []

    for linha in resultado["output"].splitlines():

        if "/tcp" not in linha:
            continue

        if "open" not in linha:
            continue

        servicos.append(
            re.sub(r"\s+", " ", linha).strip()
        )

    return servicos

def get_ports_and_services(ip):

    resultado = run([
        "nmap",
        "-sV",
        "--host-timeout",
        "30s",
        "--top-ports",
        "30",
        ip
    ], timeout=35)

    portas = []
    servicos = []

    for linha in resultado["output"].splitlines():

        if "/tcp" not in linha or "open" not in linha:
            continue

        linha_limpa = re.sub(r"\s+", " ", linha).strip()

        match = re.search(r"(\d+)/tcp\s+open", linha)

        if match:
            portas.append(match.group(1))

        servicos.append(linha_limpa)

    return {
        "ports": portas,
        "services": servicos
    }

def get_snmp(ip):

    resultado = run([
        "snmpget",
        "-v2c",
        "-c",
        "public",
        "-t",
        "1",
        "-r",
        "1",
        ip,
        "1.3.6.1.2.1.1.1.0"
    ],  timeout=5)

    if resultado:
        return resultado["output"].strip()

    return ""

def get_device_info(ip, mac):
    os_name = ""
    device_type = ""

    for counter in range(6):
        resultado = run([
            "nmap",
            "-O",
            "--osscan-guess",
            "--host-timeout",
            "30s",
            ip
        ], timeout=35)

        if "Host is up" in resultado["output"]:

            os_match = re.search(r"Running:\s*(.+)", resultado["output"])
            if not os_match:
                os_match = re.search(r"Aggressive OS guesses:\s*([^,]+)", resultado["output"])
            if not os_match:
                os_match = re.search(r"V=([^%]+)", resultado["output"])

            os_name = os_match.group(1) if os_match else ""
            if os_name:
                logging.info(
                    "Sistema operacional identificado para %s: %s",
                    ip,
                    os_name
                )

            if device_type == "":
                type_match = re.search(
                    r"Device type:\s*(.+)",
                    resultado["output"]
                )
                device_type = type_match.group(1) if type_match else ""
                if device_type:
                    logging.info(
                        "Tipo de dispositivo identificado para %s: %s",
                        ip,
                        device_type
                    )

            if os_name and device_type:
                return {
                    "os": os_name,
                    "type": device_type
                }

    if device_type == "":
        if mac:
            device_type = TYPE_DICT.get(mac.upper(),"")
            if device_type != "":
                logging.info("O dispositivo %s está usando o device_type do DICT_TYPE %s. ", ip, device_type)
            else:
                logging.info("O dispositivo %s (%s) não foi encontrado no DICT_TYPE.", ip, mac)

    if os_name == "" and device_type == "":
        logging.warning(
            "Não foi possível identificar informações de %s após %d tentativas",
            ip,
            counter + 1
        )
    elif os_name == "":
        logging.warning(
            "Não foi possível identificar o sistema operacional de %s após %d tentativas",
            ip,
            counter + 1
        )
    elif device_type == "":
        logging.warning(
            "Não foi possível identificar o tipo de dispositivo de %s após %d tentativas",
            ip,
            counter + 1
        )
        
    return {
        "os": os_name if os_name else "",
        "type": device_type if device_type else ""
    }

def process_host(host):

    ip = host["ip"]

    logging.info(
        "Processando %s",
        ip
    )

    nome = host["hostname"]

    if not nome:
        nome = ip

    mac = None
    for arp_ip, arp_info in arp_data.items():
        if arp_ip == ip:
            mac = arp_info.get("mac", "")
            break

    if not mac:
        if ip == LOCALHOST:
            mac = run([
                "cat",
                "/sys/class/net/eth0/address"
            ])["output"].strip()
            logging.info(
                "MAC encontrado para %s: %s. Esse é o localhost, então não precisa de arp-scan.",
                ip,
                mac
            )   
        else:
            mac = get_mac(ip)

    vendor = None
    for arp_ip, arp_info in arp_data.items():
        if arp_ip == ip:
            vendor = arp_info.get("vendor", "")
            break

    if mac:
        nome = MAC_DICT.get(mac.upper(), nome)
    else:
        nome = ip
    
    if mac and (not vendor or vendor.lower() == "(unknown)"):
        vendor = VENDOR_DICT.get(mac.upper(), vendor)
        logging.info(
            "Vendor atualizado  %s (%s)",
            vendor,
            mac
        )

    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ip_history = []

    if mac:
        if mac in HISTORY:

            ips_antigos = HISTORY[mac]["ips"]

            if ip not in ips_antigos and len(ips_antigos) > 0:

                logging.info(
                    "IP alterado para %s (%s). Historico: %s",
                    nome,
                    ip,
                    ", ".join(ips_antigos)
                )

        if mac not in HISTORY:
            HISTORY[mac] = {
                "nome": nome,
                "Fabricante": vendor,
                "primeira_vez": agora,
                "ultima_vez": agora,
                "ips": [ip]
            }
        else:
            if ip not in HISTORY[mac]["ips"]:
                HISTORY[mac]["ips"].append(ip)
            HISTORY[mac]["ultima_vez"] = agora
            HISTORY[mac]["nome"] = nome
            HISTORY[mac]["Fabricante"] = vendor
        ip_history = HISTORY[mac]["ips"]

    logging.info("%s ping", ip)
    latency = get_latency(ip)
    HISTORY[mac]["latency_ms"] = latency["latency"]

    logging.info("%s ports and services", ip)
    info = get_ports_and_services(ip)
    portas = info["ports"]
    servicos = info["services"]

    logging.info("%s device info", ip)
    device_info = get_device_info(ip, mac)
    os_name = device_info.get("os", "") 
    device_type = device_info.get("type", "")
    try:
        if HISTORY[mac]["device_type"] != "" and HISTORY[mac]["device_type"] != "Unknown" and device_type == "":
            device_type = HISTORY[mac]["device_type"]
        else:
            HISTORY[mac]["device_type"] = device_type
    except Exception as e:
            logging.warning("device_type not exists in HISTORY. Adding %s", e)
            HISTORY[mac]["device_type"] = device_type

    try:
        if HISTORY[mac]["os"] != "" and HISTORY[mac]["os"] != "Unknown" and os_name == "":
            os_name = HISTORY[mac]["os"]
        else:
            HISTORY[mac]["os"] = os_name
    except Exception as e:
            logging.warning("OS not exists in HISTORY. Adding %s", e)
            HISTORY[mac]["os"] = os_name

    logging.info("%s snmp", ip)
    snmp_desc = get_snmp(ip)

    inventory = {
        "ip": ip,
        "hostname": nome,
        "mac": mac,
        "vendor": vendor,
        "device_type": device_type,
        "previous_ips": ip_history,
        "latency_ms": latency["latency"],
        "os": os_name,
        "open_ports": portas,
        "services": servicos,
        "snmp_sysdescr": snmp_desc
    }

    prometheus_target = {
        "targets": [ip],
        "labels": {
            "friendly_name": nome,
            "ip": ip,
            "mac": mac,
            "vendor": vendor,
            "device_type": device_type,
            "os": os_name,
            "latency_ms": latency["latency"],
            "network": REDE
        }
    }

    return inventory, prometheus_target


inventory = []
prometheus_targets = []

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

with ThreadPoolExecutor(max_workers=20) as pool:

    futures = [
        pool.submit(process_host, h)
        for h in hosts
    ]

    for future in as_completed(futures):

        try:
            inventario, target = future.result()
        except Exception as e:
            logging.exception(e)
            continue

        inventory.append(inventario)
        prometheus_targets.append(target)
        
#
# inventory completo
#
with open(
    INVENTORY_FILE,
    "w"
) as f:

    json.dump(
        inventory,
        f,
        indent=2,
        ensure_ascii=False
    )

#
# file_sd_config do Prometheus
#
with open(
    PROMETHEUS_FILE,
    "w"
) as f:

    json.dump(
        prometheus_targets,
        f,
        indent=2,
        ensure_ascii=False
    )

with open(HISTORY_FILE, "w") as f:
    json.dump(
        HISTORY,
        f,
        indent=2,
        ensure_ascii=False
    )

with open(TELEGRAF_CONF, "w") as f:

    for host in inventory:
        f.write("[[inputs.ping]]\n")
        f.write("  urls = [\n")
        ip = host["ip"]
        f.write(f'    "{ip}",\n')
        f.write("  ]\n")
        f.write("  count = 3\n")
        f.write("  timeout = 2.0\n\n")
        f.write("  [inputs.ping.tags]\n")
        nome = host["hostname"]
        f.write("    device = \"" + nome + "\"\n\n")


logging.info(
    "Arquivo Telegraf atualizado com %s hosts.",
    len(inventory)
)

subprocess.run(["systemctl", "restart", "telegraf"], check=False)

logging.info(
    "Serviço Telegraf reiniciado."
)

logging.info(
    "Discovery finalizado. %s hosts.",
    len(inventory)
)

exec_time = timedelta(seconds=int(perf_counter()-init_timer))

logging.info(
    f"Tempo total de execução: {exec_time}"
)