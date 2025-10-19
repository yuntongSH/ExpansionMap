# 🎯 Azure Deployment - Next Steps

## ✅ Repository Ready!

All configuration files have been committed and pushed to GitHub. Your repository is now ready for Azure Static Web Apps deployment!

---

## 🚀 Deploy Now - Follow These Steps

### Step 1: Open Azure Portal
👉 Go to: **https://portal.azure.com**
- Sign in with your Microsoft account

### Step 2: Create Static Web App
1. Click the **"+ Create a resource"** button (top left)
2. In the search box, type: **"Static Web Apps"**
3. Click on **"Static Web Apps"** from the results
4. Click the blue **"Create"** button

### Step 3: Fill Out the Form

#### **Project Details:**
```
Subscription:     [Your Azure subscription]
Resource Group:   [Create new] → Type: rg-bioco2-map
```

#### **Static Web App Details:**
```
Name:             bioco2-expansion-map
Plan type:        Free (0 €/month) ✓
Region:           West Europe (or your preferred region)
```

#### **Deployment Details:**
```
Source:           GitHub ✓
GitHub Account:   [Click "Sign in with GitHub" if needed]
Organization:     yuntongSH
Repository:       ExpansionMap
Branch:           main
```

#### **Build Details:**
```
Build Presets:    Custom ✓
App location:     /
Api location:     (leave empty)
Output location:  /
```

### Step 4: Review and Create
1. Click **"Review + create"** (bottom of page)
2. Azure will validate your settings (should show ✓ Validation passed)
3. Click **"Create"** (blue button)

### Step 5: Wait for Deployment
- Azure will show a deployment progress screen
- This takes approximately **2-5 minutes**
- You'll see: "Your deployment is complete" ✓

### Step 6: Get Your URL
1. Click **"Go to resource"**
2. At the top of the page, you'll see your URL:
   ```
   https://[random-name].azurestaticapps.net
   ```
3. **Click the URL to open your map!** 🎉

---

## 🔗 Share with Your Team

Once deployed, anyone with the URL can access the map:

```
Example URL: https://nice-rock-0a1b2c3d4.1.azurestaticapps.net
```

**Send this URL to:**
- ✅ Your colleagues at EIFFEL IG
- ✅ Management team
- ✅ Business development team
- ✅ Anyone who needs access to the BioCO₂ expansion map

---

## 🎨 What Your Team Will See

When they open the URL, they'll have access to:

1. **Interactive Map** with 7,938+ sites across Europe
2. **Operator Search** - Find sites by operator name
3. **Isochrone Zones** - 30min, 1h, 2h truck drive times
4. **Opportunity Heatmap** - Visual opportunity analysis
5. **Collapsible Filters** - Supply, Offtake, Competitors
6. **Real-time Updates** - Every git push updates the live site

---

## ⚙️ Automatic Updates

From now on, whenever you:
1. Make changes to the map (edit `generate_map.py`)
2. Regenerate the HTML (`python generate_map.py --csv map.csv`)
3. Commit and push to GitHub

**Azure will automatically:**
- Detect the changes
- Rebuild the site
- Deploy updates
- Live in ~2 minutes ⚡

Check deployment status: **GitHub → Actions tab**

---

## 🔐 Optional: Add Company-Only Access

If you want to restrict access to EIFFEL IG employees only:

### Method 1: Azure AD Authentication (Recommended)
1. Azure Portal → Your Static Web App
2. Click **"Role management"** (left menu)
3. Click **"+ Add"**
4. Select **"Invite users"**
5. Choose **"Azure Active Directory"**
6. Select your company's Azure AD tenant

### Method 2: Share Link Privately
- Simply don't publish the URL publicly
- Share only via email/Teams to authorized personnel
- Azure URLs are hard to guess (cryptographically secure)

---

## 📊 Monitor Your Deployment

### Check Build Status
- **GitHub**: https://github.com/yuntongSH/ExpansionMap/actions
  - Green ✓ = Success
  - Red ✗ = Failed (check logs)

### View Azure Metrics
- Azure Portal → Your Static Web App → "Metrics"
  - Page views
  - Bandwidth usage
  - Request count

---

## 💡 Pro Tips

### Custom Domain
Want to use `map.eiffel-ig.com` instead of the Azure default URL?
1. Azure Portal → Your Static Web App → "Custom domains"
2. Follow the wizard to add your domain
3. Azure provides free SSL automatically

### Backup URL
Azure gives you a stable URL that never changes. Bookmark it!

### Mobile Access
The map works perfectly on tablets and phones! Share the URL via Teams or email.

---

## 🆘 Troubleshooting

### "I don't see the GitHub option"
- Make sure you're signed into GitHub in your browser
- Azure needs permission to access your repositories

### "Deployment failed"
- Check GitHub Actions logs for details
- Most common issue: Wrong app/output location (should be `/`)

### "Map loads but isochrones don't work"
- They should work perfectly now (HTTPS + proper CORS)
- Check browser console (F12) for any errors

### "I want to change the map name"
- You can't rename after creation
- But you can add a custom domain for a branded URL

---

## 📞 Need Help?

- **Azure Documentation**: https://learn.microsoft.com/azure/static-web-apps/
- **GitHub Actions Logs**: Check your repository's Actions tab
- **Azure Support**: Available in Azure Portal (chat icon)

---

## ✅ Checklist

Before sharing with your team, verify:

- [ ] Map loads successfully at your Azure URL
- [ ] Operator search works (try searching "bio")
- [ ] Isochrones appear when clicking sites
- [ ] Opportunity heatmap toggles on/off
- [ ] All filters (Supply, Offtake, Competitors) work
- [ ] Site count updates correctly
- [ ] Mobile view works (test on phone)

---

**Ready to deploy? Head to Azure Portal now!** 🚀

**Next: Copy this URL when deployment completes and share with your team!**
