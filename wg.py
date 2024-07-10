import discord
import config
import io
from pymongo import MongoClient 
import nacl
from nacl.public import PrivateKey
import random
from textwrap import dedent

mongo_client = MongoClient(config.MONGO_CLIENT)
db = mongo_client[config.MONGO_DB_NAME]
subs_col = db["subscriptionInfo"]


def assign_config(netid):
    priv_key_1 = PrivateKey.generate().encode(encoder=nacl.encoding.Base64Encoder).decode('ascii')
    priv_key_2 = PrivateKey.generate().encode(encoder=nacl.encoding.Base64Encoder).decode('ascii')

    existing_ips = set(doc['ipv4_addr'] for doc in subs_col.find({}, {"ipv4_addr": 1}) if 'ipv4_addr' in doc)

    while True:
        ipv4_addr = f"10.137.{random.randint(0,15)}.{random.randint(10,126)*2}"
        if ipv4_addr not in existing_ips:
            break

    subs_col.update_one(
        {"_id": netid},
        {
            "$set": {
                "priv_key_1": priv_key_1,
                "priv_key_2": priv_key_2,
                "ipv4_addr": ipv4_addr
            }
        }
    )
    #TODO: Configure Server Side

async def send_config(ctx, netid):
    sub_doc = subs_col.find_one({"_id": netid})

    ipv4_addr_1 = sub_doc.get("ipv4_addr")
    ipv4_addr_2 = '.'.join(ipv4_addr_1.split('.')[:-1] + [str(int(ipv4_addr_1.split('.')[-1]) + 1)])
    priv_key_1 = sub_doc.get("priv_key_1")
    priv_key_2 = sub_doc.get("priv_key_2")

    config1 = dedent(f"""
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

    config2 = dedent(f"""
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
    config2_obj = io.StringIO(config1)
    config2_obj.name = f"{netid}_B.conf"

    await ctx.send("Here are the configs:", files=[discord.File(config1_obj), discord.File(config2_obj)])

def key_rotate(ctx, netid):
    return