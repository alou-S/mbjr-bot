import discord
import config
import io
from pymongo import MongoClient 
import nacl
from nacl.public import PrivateKey
import random
from textwrap import dedent
import base64
import subprocess
import os

mongo_client = MongoClient(config.MONGO_CLIENT)
db = mongo_client[config.MONGO_DB_NAME]
subs_col = db["subscriptionInfo"]

def wg_genkey():
    priv_key = PrivateKey.generate()
    return base64.b64encode(priv_key.encode()).decode('ascii')

def wg_pubkey(priv_key_str):
    priv_key_bytes = base64.b64decode(priv_key_str)
    priv_key = PrivateKey(priv_key_bytes)
    pub_key = priv_key.public_key
    return base64.b64encode(pub_key.encode()).decode('ascii')


def assign_config(netid):
    priv_key_1 = wg_genkey()
    pub_key_1 = wg_pubkey(priv_key_1)
    priv_key_2 = wg_genkey()
    pub_key_2 = wg_pubkey(priv_key_2)

    existing_ips = set(doc['ipv4_addr'] for doc in subs_col.find({}, {"ipv4_addr": 1}) if 'ipv4_addr' in doc)

    while True:
        ipv4_addr_1 = f"10.137.{random.randint(0,15)}.{random.randint(10,126)*2}"
        if ipv4_addr_1 not in existing_ips:
            break

    subs_col.update_one(
        {"_id": netid},
        {
            "$set": {
                "priv_key_1": priv_key_1,
                "priv_key_2": priv_key_2,
                "ipv4_addr": ipv4_addr_1
            }
        }
    )

    ipv4_addr_2 = '.'.join(ipv4_addr_1.split('.')[:-1] + [str(int(ipv4_addr_1.split('.')[-1]) + 1)])

    with open(config.WG_CONF, 'a') as file:
        file.write(dedent(f"""
        #{netid}_A
        [Peer]
        PublicKey = {pub_key_1}
        AllowedIPs = {ipv4_addr_1}/32

        #{netid}_B
        [Peer]
        PublicKey = {pub_key_2}
        AllowedIPs = {ipv4_addr_2}/32
        """))

    subprocess.run(['sudo', f'{os.environ['HOME']}/scripts/wg-syncconf'], check=True)


async def send_config(ctx, netid):
    sub_doc = subs_col.find_one({"_id": netid})

    ipv4_addr_1 = sub_doc.get("ipv4_addr")
    ipv4_addr_2 = '.'.join(ipv4_addr_1.split('.')[:-1] + [str(int(ipv4_addr_1.split('.')[-1]) + 1)])
    priv_key_1 = sub_doc.get("priv_key_1")
    priv_key_2 = sub_doc.get("priv_key_2")

    config1 = dedent(f"""\
    [Interface]
    Address = {ipv4_addr_1}/32
    DNS = {config.WG_DNS}
    PrivateKey = {priv_key_1}
    MTU = 1400

    [Peer]
    PublicKey = {config.WG_SERVER_PUBKEY}
    AllowedIPs = {config.WG_AIPS}
    PersistentKeepalive = 25
    """)

    config2 = dedent(f"""\
    [Interface]
    Address = {ipv4_addr_2}/32
    DNS = {config.WG_DNS}
    PrivateKey = {priv_key_2}
    MTU = 1400

    [Peer]
    PublicKey = {config.WG_SERVER_PUBKEY}
    AllowedIPs = {config.WG_AIPS}
    PersistentKeepalive = 25
    """)

    config1_obj = io.StringIO(config1)
    config1_obj.name = f"{netid}_A.conf"
    config2_obj = io.StringIO(config2)
    config2_obj.name = f"{netid}_B.conf"

    await ctx.send("Here are the configs:", files=[discord.File(config1_obj), discord.File(config2_obj)])


def key_rotate(ctx, netid):
    return


def enable_netid(netid):
    return

def disable_netid(netid):
    return