# 📦 GitHub Release Instructions

## ✅ Status

- ✅ Tag created: `v0.2.4-optimized`
- ✅ Binaries created:
  - `miner-ori-windows-x64.zip` (10.23 MB)
  - `ORINode-windows-x64.zip` (77.59 MB)
- ✅ Release notes written: `dist/RELEASE_NOTES.md`

---

## 🚀 Create Release (Manual Steps)

### Step 1: Go to GitHub Releases

1. Open: https://github.com/mrnobody-dev/ORI/releases
2. Click **"Draft a new release"**

### Step 2: Configure Release

**Choose a tag:**
```
v0.2.4-optimized
```
(Should be in dropdown - already pushed)

**Release title:**
```
ORI v0.2.4 - Pool Optimized Release
```

**Description:**

Copy from `dist/RELEASE_NOTES.md` or paste:

```markdown
# 🚀 ORI v0.2.4 - Pool Optimized Release

## 🎯 Major Improvements

### Pool Performance Optimization
- ✅ Pool difficulty: **2x easier than solo** (shift=1)
- ✅ Block finding rate: **SAME as solo** (3.69s average)
- ✅ Marketing: "Pool 2x lebih mudah dari solo mining!"
- ✅ PPLNS fairness: Your work = Your reward

### Payout System
- ✅ Manual payout via pool_payout.py
- ✅ Complete documentation
- ✅ Security best practices

## 📦 Downloads

### Miner (CPU Mining)
- **miner-ori-windows-x64.zip** (10.23 MB)
  - CPU miner for pool & solo mining
  - Supports up to 64 threads
  - Windows x64 executable

### Full Node
- **ORINode-windows-x64.zip** (77.59 MB)
  - Complete ORI blockchain node
  - HTTP API included
  - P2P network support

## 🚀 Quick Start

### Pool Mining:
```bash
miner-ori.exe --address YOUR_ADDRESS --host ori-production-7cf3.up.railway.app --port 443 --threads 8 --pool --https
```

## 📊 Pool Information

- Pool URL: https://ori-production-7cf3.up.railway.app
- Fee: 1.5%
- PPLNS Window: 5,000 shares
- Minimum Payout: 0.5 ORI

**Gotong royong TIDAK melambat - MALAH LEBIH MUDAH 2X!** 🚀
```

### Step 3: Attach Binaries

Click **"Attach binaries by dropping them here or selecting them"**

Upload these 2 files from `dist/` folder:
1. `miner-ori-windows-x64.zip`
2. `ORINode-windows-x64.zip`

### Step 4: Publish

- [ ] Check **"Set as the latest release"**
- [ ] Click **"Publish release"**

---

## 🎯 Alternative: Via GitHub CLI

If you have GitHub CLI installed:

```bash
cd dist
gh release create v0.2.4-optimized \
  miner-ori-windows-x64.zip \
  ORINode-windows-x64.zip \
  --title "ORI v0.2.4 - Pool Optimized" \
  --notes-file RELEASE_NOTES.md
```

---

## ✅ After Publishing

1. Share release link: https://github.com/mrnobody-dev/ORI/releases/tag/v0.2.4-optimized
2. Announce in community
3. Update README with release badge
4. Tweet about it! 🎉

---

**Files Location:**
- Binaries: `D:\coding\BlockchainPython\blockchain-fastapi\dist\`
- Notes: `D:\coding\BlockchainPython\blockchain-fastapi\dist\RELEASE_NOTES.md`

**Ready to publish!** 🚀
