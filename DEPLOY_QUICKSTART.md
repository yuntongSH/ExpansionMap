# Quick Start: Deploy to Azure Static Web Apps

## 🚀 Fastest Way to Deploy (5 minutes)

### Step 1: Go to Azure Portal
Open: https://portal.azure.com

### Step 2: Create Static Web App
1. Click **"+ Create a resource"**
2. Search: **"Static Web Apps"**
3. Click **"Create"**

### Step 3: Fill in the Form

**Basics Tab:**
- Subscription: *(Your Azure subscription)*
- Resource Group: Create new → `rg-bioco2-map`
- Name: `bioco2-expansion-map`
- Plan: **Free** (0 €/month)
- Region: **West Europe** (closest to you)

**GitHub Integration:**
- Source: **GitHub**
- Sign in to GitHub: *(Authorize Azure)*
- Organization: `yuntongSH`
- Repository: `ExpansionMap`
- Branch: `main`

**Build Details:**
- Build Presets: **Custom**
- App location: `/`
- Output location: `/`

### Step 4: Create & Deploy
1. Click **"Review + create"**
2. Click **"Create"**
3. Wait 2-3 minutes ⏳

### Step 5: Get Your URL
After deployment completes:
- Go to the resource
- You'll see a URL like: `https://nice-rock-0a1b2c3d4.1.azurestaticapps.net`
- **Copy and share this URL with your company!** 🎉

---

## ✅ What You Get

- **Public URL**: Accessible by anyone with the link
- **HTTPS**: Automatic SSL certificate
- **Fast**: Global CDN (Content Delivery Network)
- **Auto-updates**: Every git push deploys automatically
- **Free**: No cost for your usage level

---

## 🔐 Optional: Restrict Access to Your Company

If you want only EIFFEL IG employees to access:

1. In Azure Portal → Your Static Web App
2. Go to **"Role management"**
3. Click **"Add"**
4. Select **"Azure Active Directory"**
5. Configure:
   - **Allowed users**: Your company's Azure AD
   - **Roles**: Reader (view-only)

---

## 📝 What Happens Next?

1. Azure creates a GitHub Action in your repo
2. Every time you push to `main` branch:
   - GitHub Actions runs
   - Site rebuilds
   - Deploys to Azure
   - Live in ~2 minutes

---

## 🆘 Need Help?

Contact your Azure administrator or check `AZURE_DEPLOYMENT_GUIDE.md` for detailed instructions.

---

**That's it! Your map will be online and accessible to your team! 🗺️✨**
