import time
from datetime import datetime, timedelta
import discord
from discord.ext import commands
from discord import SelectOption
from discord.ui import Select, View
import random
import re
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pymongo import MongoClient
from functools import wraps
from textwrap import dedent

import config
import messages
from base36 import to_base36
from otpmail import send_otp
from wg import enable_netid, disable_netid, get_usage, key_rotate, assign_config, send_config

# 1256347035184140349 : Unverified
# 1256347101189640305 : Verified
# 1237737131439423588 : mbjr-part guild id

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

mongo_client = MongoClient(config.MONGO_CLIENT)
db = mongo_client[config.MONGO_DB_NAME]
member_col = db["memberInfo"]
trans_col = db["transactionInfo"]
subs_col = db["subscriptionInfo"]
bot_col = db["botInfo"]

def log_time():
    return time.strftime("%b %d %H:%M:%S")


def log_invalid_command(ctx):
    print(f"{log_time()} : Invalid Command Call : {ctx.command.name} {ctx.author} {ctx.guild.name if ctx.guild else 'DM'} {ctx.channel.name if isinstance(ctx.channel, discord.TextChannel) else 'DM'}")


async def db_member_verity():
    print(f"{log_time()} : DB-Guild verity check triggered")
    guild = bot.guilds[0]
    guild_member_ids = set(member.id for member in guild.members)

    # Update or add members from the guild to the database
    for member in guild.members:
        db_member = member_col.find_one({"_id": member.id})
        if db_member:
            if not db_member.get("in_guild", False):
                print(
                    f"{log_time()} : Member {member.name}, {member.id} re-joined the guild. Updated DB."
                )
                member_col.update_one(
                    {"_id": member.id},
                    {
                        "$set": {
                            "discord_name": member.name,
                            "in_guild": True,
                            "guild_join_time": int(time.time()),
                        }
                    },
                )

                if "is_verified" not in db_member or db_member["is_verified"] is False:
                    embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
                    await member.send(embed=embed)
                    await member.send("Send `!verify` to begin verification")
            else:
                member_col.update_one(
                    {"_id": member.id},
                    {
                        "$set": {
                            "discord_name": member.name,
                        }
                    },
                )
        else:
            print(
                f"{log_time()} : Member {member.name}, {member.id} not found in DB. Added"
            )
            member_col.insert_one(
                {
                    "_id": member.id,
                    "discord_name": member.name,
                    "in_guild": True,
                    "guild_join_time": int(time.time()),
                }
            )

            embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
            await member.send(embed=embed)
            await member.send("Send `!verify` to begin verification")

    # Check if members in db still exist in guild.
    db_members = member_col.find({"in_guild": True})
    for db_member in db_members:
        if db_member["_id"] not in guild_member_ids:
            print(
                f"{log_time()} : Member {db_member['discord_name']}, {db_member['_id']} no longer in guild. Updated DB."
            )
            member_col.update_one(
                {"_id": db_member["_id"]}, {"$set": {"in_guild": False}}
            )


async def sub_verity():
    print(f"{log_time()} : Subscription verity check triggered")
    today = datetime.now().date()
    subs = subs_col.find()

    for sub in subs:
        if sub['is_subscribed'] is False:
            continue

        presub = 'presub' in sub and sub['presub']

        sub_cycle = sub['sub_cycle']
        cycle_start_date_str = sub[f"cycle{sub_cycle}_start_date"]
        cycle_start_date =  datetime.strptime(cycle_start_date_str, "%Y-%m-%d").date()
        days_since_start = (today - cycle_start_date).days

        if days_since_start >= 27:
            guild = bot.guilds[0]
            netid = sub['_id']
            discord_id = int(member_col.find_one({"netid": netid}, {"_id": 1})['_id'])
            channel_name = to_base36(discord_id)
            channel = discord.utils.get(guild.text_channels, name=channel_name)

        if days_since_start == 27 and not presub:
            print(f"{log_time()} : Payment reminder sent for {netid}")
            await channel.send(f"<@{discord_id}> The subscription for NetID **{netid}** will end today.")
            await channel.send("Send `!subscribe` to pre-subscribe for next cycle")

        elif days_since_start >= 28 and not presub:
            print(f"{log_time()} : NetID {netid} has been auto disabled by sub_verity.")
            await channel.send(f"<@{discord_id}> The subscription for NetID **{netid}** has ended.")
            await channel.send("Send `!subscribe` to subscribe for next cycle")

            disable_netid(netid, cycle=True)

        elif days_since_start >= 28 and presub:
            subs_col.update_one(
            {"_id": netid},
            {'$inc': {"sub_cycle": 1}},
            {'$set': {"presub": False}},
            )
            print(f"{log_time()} : NetID {netid} has been auto automatically resubscribed by sub_verity.")
            await channel.send(f"<@{discord_id}> NetID **{netid}** has automatically been resubscribed.")


async def verify_email(ctx):
    if subs_col.count_documents({"is_subscribed": True}) > config.MAX_SUBS:
        ctx.send("We are currently at max subscriptions! No new verifications can be taken.\nSorry for the inconvenience.")
        return

    member_doc = member_col.find_one({"_id": ctx.author.id})
    if member_doc.get("verify_fail_count", 0) > 2:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} verification rejected. (Too many attempts)")
        await ctx.send("You have failed to verify too many times. Please contact admin.")
        return False

    await ctx.send("Please enter SRM Net ID")

    netid = await text_input(ctx, title="SRM NetID", label="Please enter NetID", min_length=6, max_length=6)
    if netid is None:
        await ctx.send("No response received. The operation has been cancelled.")
        return

    if not re.match(r'^[a-z]{2}\d{4}$', netid):
        await ctx.send("Invalid NetID. Please try again.")
        return False
    member_col.update_one(
        {"_id": ctx.author.id},
        {'$inc': {"verify_fail_count": 1}}
    )

    if member_col.find_one({"netid": netid}):
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} used existing NetID {netid} for verification.")
        await ctx.send("Invalid NetID. Please try again.")
        return False

    otp = str(random.randint(100000, 999999))
    response = send_otp(otp, netid)

    print(f"{log_time()} : OTP {otp} for {ctx.author.name} {ctx.author.id} sent to NetID {netid} with response {response.text} ({response.status_code}).")
    await ctx.send(f"An OTP has been sent to the email associated with {netid}. Please send the 6-digit OTP")
    otp_msg = await text_input(ctx, title="OTP Verification", label="Please enter OTP", min_length=6, max_length=6, timeout=300)
    if otp_msg is None:
        await ctx.send("No response received. The operation has been cancelled.")
        return

    if otp_msg == otp:
        member_col.update_one(
            {"_id": ctx.author.id},
            {'$set': {"verify_fail_count": 0}}
        )

        member_col.update_one(
            {"_id": ctx.author.id},
            {
                "$push": {
                    "netid": netid
                }
            }
        )

        print(f"{log_time()} : NetID {netid} verified with Member {ctx.author.name} {ctx.author.id}.")
        await ctx.send(f"OTP has been verified for NetID {netid}")
        return True
    else:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} with NetID {netid} verification failed. (Invalid OTP)")
        await ctx.send("Incorrect OTP. Verification failed. Please try again.")
        return False


async def verify_member(ctx):
    print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} triggered verification.")
    if await verify_email(ctx) is not True:
        return

    guild = bot.guilds[0]
    unverified_role = discord.utils.get(guild.roles, id=1256347035184140349)
    verified_role = discord.utils.get(guild.roles, id=1256347101189640305)
    member = guild.get_member(ctx.author.id)

    await member.remove_roles(unverified_role)
    await member.add_roles(verified_role)

    member_col.update_one(
        {"_id": ctx.author.id},
        {
            "$set": {
                "is_verified": True
            }
        }
    )

    count = 1
    while True:
        category = discord.utils.get(guild.categories, name=f'Subscriptions{count:02d}')
        if category is None:
            category = await guild.create_category(name=f'Subscriptions{count:02d}')
            break
        elif len(category.channels) > 49:
            count+=1
        else:
            break

        
    overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True)}
    channel_name = to_base36(member.id)
    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
    print(f"{log_time()} : User verified and channel {channel_name} created for {ctx.author.name} {ctx.author.id}.")


    channel_link = f"https://discord.com/channels/{guild.id}/{channel.id}"

    await ctx.send("Account verification successful! The above NetID has been bound to your account as primary NetID.")
    await ctx.send(f"Click on the following channel to continue : {channel_link}")

    embed = discord.Embed(title="Channel Commands", description=messages.channel_cmds, color=discord.Color.blue())
    await channel.send(embed=embed)
    await channel.send("Send `!subscribe` to continue with getting subscription")


def human_bytes(bytes):
    if bytes < 1024:
        return f"{bytes} B"
    elif bytes < 1024 ** 2:
        return f"{bytes / 1024:.2f} KB"
    elif bytes < 1024 ** 3:
        return f"{bytes / (1024 ** 2):.2f} MB"
    elif bytes < 1024 ** 4:
        return f"{bytes / (1024 ** 3):.2f} GB"
    else:
        return f"{bytes / (1024 ** 4):.2f} TB"


def admin_channel_command():
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            if ctx.guild and ctx.channel.name == 'bot-admin-cmds':
                return await func(ctx, *args, **kwargs)
            else:
                log_invalid_command(ctx)
                return
        return wrapper
    return decorator


def dm_command():
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            if isinstance(ctx.channel, discord.DMChannel):
                return await func(ctx, *args, **kwargs)
            else:
                log_invalid_command(ctx)
                return
        return wrapper
    return decorator


def sub_channel_command():
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            if ctx.guild and ctx.channel.category and ctx.channel.category.name.startswith('Subscriptions'):
                return await func(ctx, *args, **kwargs)
            else:
                log_invalid_command(ctx)
                return
        return wrapper
    return decorator


@bot.event
async def on_member_join(member):
    print(f"{log_time()} : {member.name} with id {member.id} joined")
    guild = member.guild
    unverified_role = discord.utils.get(guild.roles, id=1256347035184140349)
    await member.add_roles(unverified_role)

    member_col.update_one(
        {"_id": member.id},
        {
            "$set": {
                "discord_name": member.name,
                "in_guild": True,
                "guild_join_time": int(time.time()),
            }
        },
        upsert=True,
    )

    member_doc = member_col.find_one({"_id": member.id})

    if "is_verified" not in member_doc or member_doc["is_verified"] is False:
        embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
        await member.send(embed=embed)
        await member.send("Send `!verify` to begin verification")


@bot.event
async def on_member_remove(member):
    print(f"{log_time()} : {member.name} with id {member.id} left")
    member_col.update_one({"_id": member.id}, {"$set": {"in_guild": False}})


@bot.event
async def on_ready():
    print(f"{log_time()} : Logged on as {bot.user}")

    await db_member_verity()
    botinfo = bot_col.find_one({"primary_key": "primary_key"})
    last_sub_verity_str = botinfo['last_sub_verity']
    last_sub_verity =  datetime.strptime(last_sub_verity_str, "%Y-%m-%d").date()
    today = datetime.now().date()

    if today > last_sub_verity:
        await sub_verity()

    bot_col.update_one(
        {"primary_key": "primary_key"},
        {
            "$set": {
                "last_sub_verity": time.strftime("%Y-%m-%d")
            }
        }
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(sub_verity, 'cron', hour=0, minute=0)
    scheduler.start()


@bot.command(name='verify')
@dm_command()
async def verify_member_cmd(ctx):
    member_doc = member_col.find_one({"_id": ctx.author.id})
    if "is_verified" not in member_doc or member_doc["is_verified"] is False:
        await verify_member(ctx)
    else:
        await ctx.send("You are already verified.")


@bot.command(name='db-member-verity')
@admin_channel_command()
async def db_member_verity_cmd(ctx):
    await db_member_verity()
    print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} manually called DB-Member verity.")
    await ctx.send("DB Member Verity check triggered")


@bot.command(name='sub-verity')
@admin_channel_command()
async def sub_verity_cmd(ctx):
    print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} manually called Subscription verity.")
    await sub_verity()
    await ctx.send("Subscription Verity check triggered")


@bot.command(name='add-netid')
@sub_channel_command()
async def add_netid_cmd(ctx):
    if await verify_email(ctx) is True:
        ctx.send("The above netid has been successfully added to your account.")


@bot.command(name='remove-netid')
@sub_channel_command()
async def remove_netid_cmd(ctx):
    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    netid_list.pop(0)
    if len(netid_list) < 1:
        ctx.send("You have no netid's that can be removed.")

    netid = await dropdown_select(ctx, netid_list, prompt="Select which netid to remove from your account")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    await   ctx.send("**By removing this netid, it's configs will be __disabled__ and any active subscription will be __cancelled__.**")

    bool_list = ["No, I have changed my mind", "Yes, I still want to continue."]
    bool_reply = await dropdown_select(ctx, bool_list, prompt="Do you still want to continue?")

    if bool_reply == bool_list[1]:
        try:
            disable_netid(netid, cycle=True)
        except:
            pass

        member_col.update_one(
            {"_id": ctx.author.id},
            {"$pull": {"netid": netid}}
        )

        print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} removed NetID {netid} from their account.")
        await ctx.send(f"Netid {netid} has been successfully removed from the account")
    else:
        await ctx.send("Action cancelled. Have a nice day")


@bot.command(name='subscribe')
@sub_channel_command()
async def subscribe_cmd(ctx):
    if subs_col.count_documents({"is_subscribed": True}) > config.MAX_SUBS:
        ctx.send("We are currently at max subscriptions! No new subscription can be taken.\nSorry for the inconvenience.")
        return

    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    presub_netid = []

    for sub in subs_col.find():
        if sub['is_subscribed'] is False:
            continue
        
        today = datetime.now().date()
        sub_cycle = sub['sub_cycle']
        cycle_start_date_str = sub[f"cycle{sub_cycle}_start_date"]
        cycle_start_date =  datetime.strptime(cycle_start_date_str, "%Y-%m-%d").date()
        days_since_start = (today - cycle_start_date).days

        if days_since_start == 27:
            presub_netid.append(sub['_id'])
    
    unsub_netid = [netid for netid in netid_list if not subs_col.find_one({'_id': netid, 'is_subscribed': True})]
    netid_list = presub_netid + unsub_netid

    if not netid_list:
        await ctx.send("All your NetIDs are already subscribed.")
        return

    netid = await dropdown_select(ctx, netid_list, prompt="Select which netid to activate")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
    await ctx.send(embed=embed)
    await ctx.send(f"Please send Rs. 80 to {config.UPI_ID}")
    await ctx.send("Please enter UTR (UPI Transaction ID) of your payment:")

    utr = await text_input(ctx, title="UPI Transaction ID", label="Please enter UTR", min_length=12, max_length=12, timeout=300)
    if utr is None:
        await ctx.send("No response received. The operation has been cancelled.")
        return

    try:
        utr = int(utr)
    except ValueError:
        await ctx.send("Content sent was not a integer. Please try again.")
        return

    overage = 0
    sub_doc = subs_col.find_one({"_id": netid})

    if sub_doc is not None:
        sub_cycle = sub_doc['sub_cycle']
        ipv4 = sub_doc['ipv4_addr']
        cyc_st = sub_doc[f'cycle{sub_cycle}_start_date']
        cyc_end = (datetime.strptime(cyc_st, "%Y-%m-%d") + timedelta(days=28)).strftime("%Y-%m-%d")

        data = get_usage(ipv4, cyc_st, cyc_end)
        download_sum = data[0] + data[2]
        upload_sum = data[1] + data[3]

        if max(download_sum, upload_sum) > config.VPN_MAX_DATA * 1073741824:
            overage = round(((max(download_sum, upload_sum) / (config.VPN_MAX_DATA * 1073741824)) * 80)) - 80

    amount = 80 + overage

    trans_doc = trans_col.find_one({'UTR': utr})
    if trans_doc is None:
        print(f"{log_time()} : Non existent UTR {utr} used by user {ctx.author.name} {ctx.author.id} for NetID {netid}")
        await ctx.send("Transaction not found. Please try again")
        return
    elif trans_doc.get('is_claimed', False) is True:
        print(f"{log_time()} : Duplicate UTR {utr} used by user {ctx.author.name} {ctx.author.id} for NetID {netid}")
        await ctx.send("Duplicate UTR ID. What are you trying bro?")
        return

    trans_amount = trans_doc.get('Amount')
    if trans_amount < amount:
        print(f"{log_time()} : Underpayment of Rs. {trans_amount} instead of Rs. {amount} with UTR {utr} by user {ctx.author.name} {ctx.author.id} for NetID {netid}")
        await ctx.send(f"You payed less than {amount} rupees. Please try again")
        await ctx.send("Please contact admin to refund the transaction.")
        return
    elif trans_amount > amount:
        print(f"{log_time()} : Overpayment of Rs. {trans_amount} instead of Rs. {amount} with UTR {utr} by user {ctx.author.name} {ctx.author.id} for NetID {netid}")
        await ctx.send(f"You payed more than {amount} rupees.")
        await ctx.send("Please contact admin to refund the excess.")

    trans_col.update_one(
        {'UTR': utr},
        {
            "$set": {
                "is_claimed": True,
                "claimed_by": [netid, ctx.author.name, str(ctx.author.id)]
            }
        },
    )
    print(f"{log_time()} : UTR {utr} claimed by user {ctx.author.name} {ctx.author.id} for NetID {netid}.")

    if netid in presub_netid:
        subs_col.update_one(
            {"_id": netid},
            {
                "$set": {
                    f"cycle{sub_cycle+1}_start_date": (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
                    "presub": True,
                }
            },
        )

        await ctx.send(f"Transaction Verified\nYou have presubscribed the next cycle for {netid}.")
        await ctx.send(f"Next subscription will end on {time.strftime("%Y-%m-%d", time.localtime((time.time()) + 2419200))}")
    else:
        subs_col.update_one(
            {"_id": netid},
            {'$inc': {"sub_cycle": 1}},         
            upsert=True
        )
        print(f"{log_time()} : Key sub_cycle for NetID {netid} incremented to {subs_col.find_one({"_id": netid}).get('sub_cycle')}.")
        enable_netid(netid, cycle=True)

        if 'ipv4_addr' not in subs_col.find_one({"_id": netid}):
            print(f"{log_time()} : Configs assigned for NetID {netid} by {ctx.author.name} {ctx.author.id}.")
            assign_config(netid)

        await ctx.send(f"Transaction Verified\nVPN subscription has been enabled for {netid}.")
        await ctx.send(f"Subscription will end on {time.strftime("%Y-%m-%d", time.localtime((time.time()) + 2332800))}")
        await ctx.send("Steps to setup VPN : <https://gist.github.com/alou-S/43af98571b7b08c0c0ba51e6c54b813b>")
        await ctx.send("Send `!get-config` to get the Wireguard configs")


@bot.command(name='get-config')
@sub_channel_command()
async def get_config_cmd(ctx):
    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    sub_netid = []

    for netid in netid_list:
        sub_doc = subs_col.find_one({'_id': netid})
        if sub_doc is not None and sub_doc.get('is_subscribed', False) is True:
            sub_netid.append(netid)

    netid = await dropdown_select(ctx=ctx, item_list=sub_netid, prompt="Select which config to get")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} requested configs for NetID {netid}.")
    await ctx.send("Here are the configs:", files=send_config(netid))


@bot.command(name='get-usage')
@sub_channel_command()
async def get_usage_cmd(ctx):
    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    sub_netid = []

    for netid in netid_list:
        sub_doc = subs_col.find_one({'_id': netid})
        if sub_doc is not None and sub_doc.get('is_subscribed', False) is True:
            sub_netid.append(netid)

    netid = await dropdown_select(ctx=ctx, item_list=sub_netid, prompt="Select which config to get status for")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    sub_doc = subs_col.find_one({'_id': netid})
    sub_cycle = sub_doc['sub_cycle']
    cycle_list = [f"Cycle {i}" for i in range(sub_cycle, 0, -1)]
    cycle_str = await dropdown_select(ctx=ctx, item_list=cycle_list, prompt="Select which cycle to get status for")
    if cycle_str is None:
        await ctx.send("You didn't make a selection in time.")
        return
    cycle = sub_cycle - cycle_list.index(cycle_str)

    ipv4 = sub_doc['ipv4_addr']
    cyc_st = sub_doc[f'cycle{cycle}_start_date']
    cyc_end = (datetime.strptime(cyc_st, "%Y-%m-%d") + timedelta(days=28)).strftime("%Y-%m-%d")

    print(f"{log_time()} : User {ctx.author.name} {ctx.author.id} requested usage for NetID {netid} Cycle {cycle}.")
    data = get_usage(ipv4, cyc_st, cyc_end)
    download_sum = data[0] + data[2]
    upload_sum = data[1] + data[3]

    message = dedent(f"""\
        **Cycle**: {cycle}
        **Start Date**: {cyc_st}
        **End Date**: {cyc_end}

        ### Usage:
        **{netid}_A**:
            Download: {human_bytes(data[0])}
            Upload: {human_bytes(data[1])}

        **{netid}_B**
            Download: {human_bytes(data[2])}
            Upload: {human_bytes(data[3])}

        ### Total Usage:
            Download: {human_bytes(download_sum)} / {config.VPN_MAX_DATA} GB
            Upload:     {human_bytes(upload_sum)} / {config.VPN_MAX_DATA} GB
        """)

    if max(download_sum, upload_sum) > config.VPN_MAX_DATA * 1073741824:
        message += dedent(f"""\
            **Current Overage Cost: Rs. {round(((max(download_sum, upload_sum) / (config.VPN_MAX_DATA * 1073741824)) * 80)) - 80}**
        """)

    embed = discord.Embed(title="VPN Usage Stats", description=message, color=discord.Color.red())
    await ctx.send(embed=embed)


@bot.command(name='rotate-keys')
@sub_channel_command()
async def rotate_keys_cmd(ctx):
    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    sub_netid = []

    for netid in netid_list:
        sub_doc = subs_col.find_one({'_id': netid})
        if sub_doc is not None and sub_doc.get('is_subscribed', False) is True:
            sub_netid.append(netid)

    netid = await dropdown_select(ctx=ctx, item_list=sub_netid, prompt="Select netid for key rotation")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    print(f"{log_time()} : Key rotation triggered by {ctx.author.name} {ctx.author.id} for NetID {netid}.")
    key_rotate(netid)
    await ctx.send(f"Keys for {netid} have been successfully rotated")
    await ctx.send ("Send `!get-config` to get the new Wireguard configs")


@bot.command(name='enable-netid')
@admin_channel_command()
async def enable_netid_cmd(ctx):
    docs = member_col.find( {"netid": {"$exists": True}})
    netid_list = []
    for doc in docs:
        netid_list.extend(doc['netid'])

    netid = await dropdown_select(ctx=ctx, item_list=netid_list, prompt="Select a NetID to enable")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    print(f"{log_time()} : Admin {ctx.author.name} {ctx.author.id} enabled configs of NetID {netid}.")
    if enable_netid(netid) is False:
        await ctx.send(f"{netid} is already enabled.")
    else:
        await ctx.send(f"{netid} has been enabled.")


@bot.command(name='disable-netid')
@admin_channel_command()
async def disable_netid_cmd(ctx):
    docs = member_col.find( {"netid": {"$exists": True}})
    netid_list = []
    for doc in docs:
        netid_list.extend(doc['netid'])

    netid = await dropdown_select(ctx=ctx, item_list=netid_list, prompt="Select a NetID to disable")
    if netid is None:
        await ctx.send("You didn't make a selection in time.")
        return

    print(f"{log_time()} : Admin {ctx.author.name} {ctx.author.id} disabled configs of NetID {netid}.")
    if disable_netid(netid) is False:
        await ctx.send(f"{netid} is already disabled.")
    else:
        await ctx.send(f"{netid} has been disabled.")


#TODO: Write the help messages
bot.remove_command('help')
@bot.command(name='help')
async def help_cmd(ctx):
    if ctx.guild and ctx.channel.name == 'bot-admin-cmds':
        embed = discord.Embed(title="Admin Commands", description=messages.admin_cmds, color=discord.Color.red())
        await ctx.send(embed=embed)

    elif ctx.guild and ctx.channel.category and ctx.channel.category.name.startswith('Subscriptions'):
        embed = discord.Embed(title="Channel Commands", description=messages.channel_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    elif isinstance(ctx.channel, discord.DMChannel):
        embed = discord.Embed(title="DM Commands", description=messages.dm_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    else:
        log_invalid_command(ctx)


async def dropdown_select(ctx, item_list, prompt="Select an item", timeout=30):
    pages = [item_list[i:i+23] for i in range(0, len(item_list), 23)]
    current_page = 0
    selected_item = None

    while True:
        options = []
        if current_page > 0:
            options.append(SelectOption(label="◀️ Previous Page", value="!#prev", description="Go to the previous page"))
        options.extend([SelectOption(label=str(item), value=str(i)) for i, item in enumerate(pages[current_page])])
        if current_page < len(pages) - 1:
            options.append(SelectOption(label="Next Page ▶️", value="!#next", description="Go to the next page"))

        select = Select(placeholder=f"{prompt} (Page {current_page + 1}/{len(pages)})", options=options)
        view = View(timeout=timeout)
        view.add_item(select)

        async def select_callback(interaction):
            nonlocal selected_item, current_page
            if select.values[0] == "!#prev":
                current_page -= 1
            elif select.values[0] == "!#next":
                current_page += 1
            else:
                selected_item = pages[current_page][int(select.values[0])]
            await interaction.response.defer()
            view.stop()

        select.callback = select_callback

        message = await ctx.send(f"{prompt} (Page {current_page + 1}/{len(pages)})", view=view)
        timer_message = await ctx.send(f"Time remaining: {timeout}s")

        start_time = asyncio.get_event_loop().time()

        while timeout > 0 and not view.is_finished():
            await asyncio.sleep(2.5)
            elapsed_time = int(asyncio.get_event_loop().time() - start_time)
            remaining_time = max(0, timeout - elapsed_time)
            await timer_message.edit(content=f"Time remaining: {remaining_time}s")

            if remaining_time <= 0 or view.is_finished():
                break

        if view.is_finished() and selected_item is not None:
            await timer_message.delete()
            break

        if remaining_time <= 0:
            await message.edit(content="Selection timed out.", view=None)
            await timer_message.delete()
            return None

        await message.delete()
        await timer_message.delete()

    await message.edit(content=f"Selected: {selected_item}", view=None)
    return selected_item


async def text_input(ctx, title, label, placeholder=None, min_length=1, max_length=100, timeout=30):
    class TextInputModal(discord.ui.Modal):
        def __init__(self):
            super().__init__(title=title)
            self.text_input = discord.ui.TextInput(
                label=label[:45],
                placeholder=placeholder,
                min_length=min_length,
                max_length=max_length
            )
            self.add_item(self.text_input)

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.defer()
            self.interaction = interaction
            self.stop()

    class ResponseView(discord.ui.View):
        def __init__(self):
            super().__init__()
            self.value = None

        @discord.ui.button(label="Click to Respond", style=discord.ButtonStyle.blurple)
        async def respond(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = TextInputModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            if modal.text_input.value:
                self.value = modal.text_input.value
                self.stop()

    view = ResponseView()
    modal_message = await ctx.send("Please click the button below to provide your input.", view=view)
    timer_message = await ctx.send(f"Time remaining: {timeout} seconds")

    end_time = asyncio.get_event_loop().time() + timeout

    async def update_timer():
        while True:
            remaining_time = max(0, round(end_time - asyncio.get_event_loop().time()))
            if remaining_time <= 0:
                await timer_message.edit(content="The response time has expired.")
                break
            await timer_message.edit(content=f"Time remaining: {remaining_time} seconds")
            await asyncio.sleep(2.5)

    update_task = asyncio.create_task(update_timer())

    try:
        await asyncio.wait_for(view.wait(), timeout=timeout)
        update_task.cancel()
        await modal_message.edit(content=f"Submitted response: {view.value}", view=None)
        await timer_message.delete()
        return view.value
    except asyncio.TimeoutError:
        update_task.cancel()
        await modal_message.edit(content="The response time has expired. No response was submitted.", view=None)
        await timer_message.delete()
        return None


bot.run(config.DISCORD_BOT_TOKEN)
