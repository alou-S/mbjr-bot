import time
import discord
import random
import re
from pymongo import MongoClient
from discord.ext import commands

import config
import messages

# 1256347035184140349 : Unverified
# 1256347101189640305 : Verified
# 1237737131439423588 : mbjr-part guild id

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

mongo_client = MongoClient(config.mongo_client)
db = mongo_client[config.mongo_db]
member_col = db["memberInfo"]


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


async def verify_member(ctx):
    member_doc = member_col.find_one({"_id": ctx.author.id})
    if member_doc.get("verify_fail_count", 0) > 2:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} verification rejected. (Too many attempts)")
        await ctx.send("You have failed to verify too many times. Please contact admin.")
        return
    
    await ctx.send("Please send your SRM Net ID")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        netid_msg = await bot.wait_for('message', check=check, timeout=60.0)
        netid = netid_msg.content.lower()

        # Validate NetID format
        if not re.match(r'^[a-z]{2}\d{4}$', netid):
            await ctx.send("Invalid NetID format. Please try again with the correct format.")
            return

        if member_col.find_one({"netid": netid}):
            print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} used existing NetID {netid} for verification.")
            await ctx.send("This NetID has been already used. Please try again with a valid NetID.")
            return

        otp = str(random.randint(100000, 999999))

        print(otp)

        member_col.update_one(
            {"_id": ctx.author.id},
            {'$inc': {"verify_fail_count": 1}}
        )
        print(f"{log_time()} : OTP {otp} for {ctx.author.name} {ctx.author.id} sent to NetID {netid}.")
        await ctx.send(f"An OTP has been sent to your email associated with {netid}. Please send the 6-digit OTP:")
        otp_msg = await bot.wait_for('message', check=check, timeout=300.0)

        if otp_msg.content == otp:
            guild = bot.guilds[0]
            unverified_role = discord.utils.get(guild.roles, id=1256347035184140349)
            verified_role = discord.utils.get(guild.roles, id=1256347101189640305)
            member = guild.get_member(ctx.author.id)

            await member.remove_roles(unverified_role)
            await member.add_roles(verified_role)

            member_col.update_one(
                {"_id": ctx.author.id},
                {'$set': {"verify_fail_count": 0}}
            )

            member_col.update_one(
                {"_id": ctx.author.id},
                {
                    "$push": {
                        "netid": netid
                    },
                    "$set": {
                        "is_verified": True
                    }
                }
            )
            print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} with NetID {netid} verified.")
            await ctx.send("Verification successful! Your account has been verified.")
            return
            
        else:
            print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} with NetID {netid} verification failed. (Invalid OTP)")
            await ctx.send("Incorrect OTP. Verification failed. Please try again.")
            return

    except asyncio.TimeoutError:
        print(f"{log_time()} : Member {ctx.author.name} {ctx.author.id} verification timeout.")
        print(f"{log_time()} : ")
        await ctx.send("Verification timed out. Please try again.")


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
async def verify_member_cmd(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        member_doc = member_col.find_one({"_id": ctx.author.id})
        if "is_verified" not in member_doc or member_doc["is_verified"] == False:
            await verify_member(ctx)
        else:
            await ctx.send("You are already verified.")
    else:
        log_invalid_command(ctx)



@bot.command(name='db-member-verity')
async def db_member_verity_cmd(ctx):
    if ctx.guild and ctx.channel.name == 'bot-admin-cmds':
        db_member_verity()
        await ctx.send("DB Member Verity check triggered")
    else:
        log_invalid_command(ctx)

bot.remove_command('help')
@bot.command(name='help')
async def help_cmd(ctx):
    if ctx.guild and ctx.channel.name == 'bot-admin-cmds':
        embed = discord.Embed(title="Admin Commands", description=messages.admin_cmds, color=discord.Color.red())
        await ctx.send(embed=embed)

    elif ctx.guild and ctx.channel.category and ctx.channel.category.name == 'subscription':
        embed = discord.Embed(title="Channel Commands", description=messages.channel_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    elif isinstance(ctx.channel, discord.DMChannel):
        embed = discord.Embed(title="DM Commands", description=messages.dm_cmds, color=discord.Color.blue())
        await ctx.send(embed=embed)

    else:
        log_invalid_command(ctx)


bot.run(config.token)
