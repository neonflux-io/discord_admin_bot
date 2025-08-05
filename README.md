# 🚀 AdminBot - Advanced Discord Moderation Bot

<div align="center">

![Discord](https://img.shields.io/badge/Discord-7289DA?style=for-the-badge&logo=discord&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**A powerful Discord moderation bot with advanced features for server management, user moderation, and automated tasks.**

</div>

---

## ✨ Features

### 🔨 **Moderation Commands**
- **Ban/Unban System** - Advanced ban management with moderator tracking
- **Timeout Management** - Temporary user restrictions with duration parsing
- **Kick System** - Quick user removal with reason logging
- **Mute Commands** - Multiple mute types (regular, image, reaction)

### 🛡️ **Channel Management**
- **Lock/Unlock** - Channel access control with auto-unlock timers
- **Hide/Show** - Channel visibility management
- **Hardlock System** - Permanent channel restrictions
- **Category Management** - Bulk operations on channel categories

### 🎭 **Role Management**
- **Role Assignment** - Quick role management with aliases
- **Role Icons** - Custom role icon setting
- **Sticky Reaction Roles** - Interactive role assignment system
- **Mute Role Creation** - Automatic mute role setup

### 🎮 **Voice Channel Features**
- **Voice Master** - Advanced voice channel management
- **Private Rooms** - Drag users to private voice channels
- **Voice Permissions** - Grant/revoke voice channel access

### 🎉 **Interactive Features**
- **Giveaway System** - Automated giveaway management
- **AFK System** - Global and server-specific AFK status
- **Reaction Roles** - Interactive role assignment
- **Pagination** - Beautiful paginated embeds

---

## 🚀 Installation

### Prerequisites
- Python 3.8 or higher
- Discord Bot Token
- Required permissions

### Quick Setup

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/iztche-aimbot.git
cd iztche-aimbot
```

2. **Install dependencies**
```bash
pip install discord.py python-dotenv
```

3. **Configure environment**
```bash
# Create .env file
echo "DISCORD_TOKEN=your_bot_token_here" > .env
```

4. **Run the bot**
```bash
python bot.py
```

### Required Bot Permissions
```
✅ Administrator (Recommended)
✅ Manage Messages
✅ Manage Channels
✅ Manage Roles
✅ Ban Members
✅ Kick Members
✅ Moderate Members
✅ Send Messages
✅ Embed Links
✅ Use External Emojis
✅ Add Reactions
```

---

## 📋 Commands

### 🔨 **Moderation Commands**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,ban` | `,b` | Ban a user with reason | Ban Members |
| `,unban` | `,ub` | Unban a user | Ban Members |
| `,unbanall` | `,uba` | Unban all users | Ban Members |
| `,kick` | `,k` | Kick a user | Kick Members |
| `,timeout` | `,to`, `,t` | Timeout a user | Moderate Members |
| `,untimeout` | `,uto` | Remove timeout | Moderate Members |
| `,untimeoutall` | `,uta` | Remove all timeouts | Moderate Members |

### 🛡️ **Channel Management**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,lock` | `,l` | Lock current channel | Manage Channels |
| `,unlock` | `,ul` | Unlock current channel | Manage Channels |
| `,lockall` | - | Lock all channels | Manage Channels |
| `,unlockall` | `,ula`, `,ua` | Unlock all channels | Manage Channels |
| `,hide` | `,h` | Hide current channel | Manage Channels |
| `,unhide` | `,uh` | Show current channel | Manage Channels |
| `,hideall` | `,hall` | Hide all channels | Manage Channels |
| `,unhideall` | `,uhall` | Show all channels | Manage Channels |

### 🎭 **Role Management**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,role` | `,r` | Manage user roles | Manage Roles |
| `,roleicon` | - | Set role icon | Manage Roles |
| `,mute` | `,m` | Mute a user | Manage Roles |
| `,imute` | `,im` | Image mute user | Manage Roles |
| `,rmute` | `,rm` | Reaction mute user | Manage Roles |
| `,unmute` | - | Unmute a user | Manage Roles |

### 🎮 **Voice Commands**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,vc` | - | Voice channel management | - |
| `,drag` | `,d` | Drag user to voice | - |
| `,dragpriv` | `,dp` | Create private voice | - |

### 🎉 **Interactive Features**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,giveaways` | `,gw` | Manage giveaways | - |
| `,afk` | - | Set AFK status | - |
| `,stickyreactionrole` | `,sr` | Manage reaction roles | Manage Roles |

### 🛠️ **Utility Commands**

| Command | Aliases | Description | Permission |
|---------|---------|-------------|------------|
| `,userinfo` | `,ui`, `,whois` | Show user info | - |
| `,serverinfo` | `,si` | Show server info | - |
| `,avatar` | `,av`, `,pfp` | Show user avatar | - |
| `,banlist` | `,bl` | Show ban list | Ban Members |
| `,timeoutlist` | `,tl` | Show timeouts | Moderate Members |
| `,prefix` | - | Change bot prefix | - |

---

## ⚙️ Configuration

### Environment Variables
```env
DISCORD_TOKEN=your_bot_token_here
```

### Custom Prefixes
The bot supports custom prefixes per server:
- Default prefix: `,`
- Change with: `,prefix <new_prefix>`

### Auto-Features
- **Auto-unlock timers** - Channels automatically unlock after specified time
- **Auto-unhide timers** - Hidden channels automatically become visible
- **Sticky reaction roles** - Persistent role assignment system
- **Giveaway automation** - Automatic winner selection and notification

---

## 🎨 Advanced Features

### 🔄 **Auto-Timers**
```bash
,lock 30m    # Lock channel for 30 minutes
,hide 1h     # Hide channel for 1 hour
```

### 🎯 **Advanced Moderation**
- **Moderator tracking** - All actions are logged with moderator info
- **DM notifications** - Users receive detailed notifications
- **Reason logging** - All actions include reason tracking
- **Bulk operations** - Mass timeout/ban/unban capabilities

### 🎭 **Interactive UI**
- **Button interfaces** - Modern Discord UI components
- **Pagination** - Beautiful multi-page displays
- **Reaction systems** - Interactive role assignment
- **Embed formatting** - Professional-looking messages

### 🛡️ **Security Features**
- **Permission checks** - Comprehensive permission validation
- **Error handling** - Graceful error management
- **Rate limiting** - Built-in Discord API compliance
- **Safe operations** - All destructive actions require confirmation

---

## 🤝 Contributing

We welcome contributions! Please feel free to submit a Pull Request.

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

### Code Style
- Follow PEP 8 guidelines
- Add comments for complex logic
- Include error handling
- Test all new features

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.