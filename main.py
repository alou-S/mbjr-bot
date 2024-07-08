import time
import discord
import random
import re
import asyncio
from pymongo import MongoClient
from discord.ext import commands
from functools import wraps

import config
import messages
from base36 import *
from otpmail import send_otp

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

def log_time():
    return time.strftime("%b %d %H:%M:%S")


def log_invalid_command(ctx):
    print(f"{log_time()} : Invalid Command Call : {ctx.command.name} {ctx.author} {ctx.guild.name if ctx.guild else 'DM'} {ctx.channel.name if isinstance(ctx.channel, discord.TextChannel) else 'DM'}")


def db_member_verity():
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


async def  verify_email(ctx):
    member_doc = member_col.find_one({"_id": ctx.author.id})
    if member_doc.get("verify_fail_count", 0) > 2:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} verification rejected. (Too many attempts)")
        await ctx.send("You have failed to verify too many times. Please contact admin.")
        return False
    
    await ctx.send("Please send SRM Net ID")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        netid_msg = await bot.wait_for('message', check=check, timeout=60.0)
        netid = netid_msg.content.lower()

        # Validate NetID format
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
        otp_msg = await bot.wait_for('message', check=check, timeout=300.0)

        if otp_msg.content == otp:
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
    
    except asyncio.TimeoutError:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} verification timeout.")
        print(f"{log_time()} : ")
        await ctx.send("Verification timed out. Please try again.")
        return False


async def verify_member(ctx):
        if await verify_email(ctx) == True:
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

            category = discord.utils.get(guild.categories, name="Subscriptions")
            overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True)}
            channel_name = to_base36(member.id)
            channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

            channel_link = f"https://discord.com/channels/{guild.id}/{channel.id}"

            await ctx.send(f"Account verification successful! The above NetID has been bound to your account as primary NetID.")
            await ctx.send(f"Click on the following channel to continue : {channel_link}")


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
            if ctx.guild and ctx.channel.category and ctx.channel.category.name == 'Subscriptions':
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

    if "is_verified" not in member_doc or member_doc["is_verified"] == False:
        embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
        await member.send(embed=embed)


@bot.event
async def on_member_remove(member):
    print(f"{log_time()} : {member.name} with id {member.id} left")
    member_col.update_one({"_id": member.id}, {"$set": {"in_guild": False}})


@bot.event
async def on_ready():
    print(f"{log_time()} : Logged on as {bot.user}")

    db_member_verity()


@bot.command(name='verify')
@dm_command()
async def verify_member_cmd(ctx):
    member_doc = member_col.find_one({"_id": ctx.author.id})
    if "is_verified" not in member_doc or member_doc["is_verified"] == False:
        await verify_member(ctx)
    else:
        await ctx.send("You are already verified.")


@bot.command(name='db-member-verity')
@admin_channel_command()
async def db_member_verity_cmd(ctx):
    db_member_verity()
    await ctx.send("DB Member Verity check triggered")

@bot.command(name='add-netid')
@sub_channel_command()
async def add_netid_command(ctx):
    verify_email(ctx)

@bot.command(name='subscribe')
@sub_channel_command()
async def subscribe(ctx):
    netid_list = member_col.find_one({'_id' : ctx.author.id}).get('netid')
    unsub_netid = []

    for netid in netid_list:
        sub_doc = subs_col.find_one({'_id': netid})
        if sub_doc is None:
            unsub_netid.append(netid)
        elif sub_doc.get('is_subscribed', False) == False:
            unsub_netid.append(netid)

    message = "**Select which netid to activate __(Reply with number)__:\n**"

    for index, netid in enumerate(unsub_netid, start=1):
        message += f"{index}. {netid}\n"

    await ctx.send(message)

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        reply = await bot.wait_for('message', check=check, timeout=30.0)
        netid_index = int(reply.content)

        if not 1 <= netid_index <= len(unsub_netid):
            await ctx.send("Invalid netid index. Please try again")
            return

        netid = unsub_netid[netid_index-1]

        embed = discord.Embed(title="Memo", description=messages.memo, color=discord.Color.blue())
        await ctx.send(embed=embed)
        await ctx.send("Please enter UTR (UPI Transaction No). of your payment:")

        reply = await bot.wait_for('message', check=check, timeout=120.0)
        utr = int(reply.content)

        if len(str(utr)) != 12:
            await ctx.send("Invalid UTR (Should be 12 digits). Please try again.")
            return

        trans_doc = trans_col.find_one({'UTR': utr})

        if trans_doc == None:
            await ctx.send("Transaction not found. Please try again")
            return
        elif trans_doc.get('is_claimed', False) == True:
            await ctx.send("Duplicate UTR ID. What are you trying bro?")
            return

        trans_col.update_one(
            {'UTR': utr},
            {
                "$set": {
                    "is_claimed": True
                }
            },
        )

        subs_col.update_one(
            {"_id": netid},
            {'$inc': {"sub_cycle": 1}},         
            upsert=True
        )

        cycle = subs_col.find_one({"_id": netid}).get('sub_cycle')

        subs_col.update_one(
            {"_id": netid},
            {
                "$set": {
                    f"cycle{cycle}_start_date": time.strftime("%Y-%m-%d"),
                    "is_subscribed": True
                }       
            }
        )

        await ctx.send(f"Transaction Veirifed\nVPN subscription has been enabled for {netid}.")
        await ctx.send(f"Subscription will end on {time.strftime("%Y-%m-%d", time.localtime((time.time()) + 2419200))}")
        await ctx.send ("Use `!get-config` to get the Wireguard Configs")

    except asyncio.TimeoutError:
        await ctx.send("Timed out waiting for reply. Please try again.")
        return  

# TODO: Write the help messages
bot.remove_command('help')
@bot.command(name='help')
async def help_cmd(ctx):
    if ctx.guild and ctx.channel.name == 'bot-admin-cmds':
        embed = discord.Embed(title="Admin Commands", description=messages.admin_cmds, color=discord.Color.red())
        await ctx.send(embed=embed)

    elif ctx.guild and ctx.channel.category and ctx.channel.category.name == 'Subscriptions':
        embed = discord.Embed(title="Channel Commands", description=messages.channel_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    elif isinstance(ctx.channel, discord.DMChannel):
        embed = discord.Embed(title="DM Commands", description=messages.dm_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    else:
        log_invalid_command(ctx)


bot.run(config.DISCORD_BOT_TOKEN)
