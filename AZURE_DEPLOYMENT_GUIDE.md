# Azure Static Web Apps Deployment Guide

## Prerequisites
- Azure account (with active subscription)
- GitHub account (already connected to ExpansionMap repository)
- Azure CLI (optional, for command-line deployment)

## Deployment Steps

### Option 1: Deploy via Azure Portal (Recommended - Easiest)

#### Step 1: Create Static Web App
1. Go to [Azure Portal](https://portal.azure.com)
2. Click **"Create a resource"**
3. Search for **"Static Web Apps"**
4. Click **"Create"**

#### Step 2: Basic Configuration
Fill in the following:
- **Subscription**: Select your Azure subscription
- **Resource Group**: Create new or use existing (e.g., "rg-bioco2-map")
- **Name**: `bioco2-expansion-map` (or your preferred name)
- **Plan type**: **Free** (perfect for your use case)
- **Region**: Choose closest to your company (e.g., "West Europe")

#### Step 3: Deployment Details
- **Source**: Select **GitHub**
- **Sign in to GitHub**: Authenticate if needed
- **Organization**: `yuntongSH`
- **Repository**: `ExpansionMap`
- **Branch**: `main`

#### Step 4: Build Details
- **Build Presets**: Select **"Custom"**
- **App location**: `/` (root directory)
- **Api location**: Leave empty
- **Output location**: `/` (root directory)

#### Step 5: Review + Create
1. Click **"Review + create"**
2. Review settings
3. Click **"Create"**

#### Step 6: Wait for Deployment
- Azure will automatically:
  - Create a GitHub Actions workflow in your repository
  - Build and deploy your site
  - Provide you with a URL (e.g., `https://bioco2-expansion-map.azurestaticapps.net`)
- First deployment takes 2-5 minutes

---

### Option 2: Deploy via Azure CLI

```bash
# Login to Azure
az login

# Create resource group (if needed)
az group create --name rg-bioco2-map --location westeurope

# Create Static Web App
az staticwebapp create \
  --name bioco2-expansion-map \
  --resource-group rg-bioco2-map \
  --source https://github.com/yuntongSH/ExpansionMap \
  --location westeurope \
  --branch main \
  --app-location "/" \
  --output-location "/" \
  --login-with-github
```

---

### Option 3: Manual GitHub Actions Setup

If you prefer to set up the workflow manually:

1. Create `.github/workflows/azure-static-web-apps.yml` (see workflow file)
2. Get your Azure Static Web Apps deployment token:
   - Go to Azure Portal → Your Static Web App → "Manage deployment token"
   - Copy the token
3. Add token to GitHub Secrets:
   - Go to GitHub → Repository Settings → Secrets and variables → Actions
   - Create new secret: `AZURE_STATIC_WEB_APPS_API_TOKEN`
   - Paste the token
4. Push to GitHub → Automatic deployment

---

## What Gets Deployed

Your Static Web App will include:
- ✅ `BioCO2 Expansion Map 2025.html` (main map)
- ✅ `staticwebapp.config.json` (routing and CORS config)
- ✅ CORS headers enabled for OpenRouteService API
- ✅ Custom routing (root `/` serves the map)

---

## Post-Deployment

### Access Your Map
After deployment, you'll get a URL like:
```
https://bioco2-expansion-map.azurestaticapps.net
```

### Custom Domain (Optional)
To use a custom domain like `map.eiffel-ig.com`:

1. In Azure Portal → Your Static Web App → "Custom domains"
2. Click **"Add"**
3. Choose **"Custom domain on other DNS"**
4. Follow instructions to:
   - Add CNAME record to your DNS
   - Validate domain
   - Configure SSL (automatic with Azure)

### Configure Access Control (Optional)

If you want to restrict access to your company only:

1. In Azure Portal → Your Static Web App → "Role management"
2. Add **Authentication provider** (Azure AD for company authentication)
3. Configure invitation-only access or restrict to your company domain

---

## Automatic Updates

Once deployed, **every push to the `main` branch** will:
1. Trigger GitHub Actions
2. Rebuild the site
3. Deploy automatically
4. Update your live map

No manual redeployment needed! 🎉

---

## Monitoring and Management

### View Deployment Status
- **GitHub**: Check Actions tab for build/deploy status
- **Azure Portal**: Monitor under "Environments" in your Static Web App

### View Logs
- Azure Portal → Your Static Web App → "Logs" → "Application Insights"

### Update Configuration
- Modify `staticwebapp.config.json` and push to GitHub

---

## Cost

- **Free Tier Includes**:
  - 100 GB bandwidth/month
  - 0.5 GB storage
  - Free SSL certificate
  - Custom domain support
  - Global CDN distribution

**Perfect for your internal company map!** 🎉

---

## Troubleshooting

### Issue: Map not loading
- Check browser console for errors
- Verify `staticwebapp.config.json` is in repository root
- Check GitHub Actions logs for deployment errors

### Issue: Isochrone API not working
- The direct API calls will work perfectly since site is served via HTTPS
- No CORS proxy needed anymore
- Check console for 200 OK responses from OpenRouteService

### Issue: 404 errors
- Verify file paths in `staticwebapp.config.json`
- Ensure `BioCO2 Expansion Map 2025.html` is in repository root

---

## Next Steps After Deployment

1. ✅ **Test the URL**: Open in browser and verify map loads
2. ✅ **Test Search**: Try operator search feature
3. ✅ **Test Isochrones**: Click site → Toggle isochrones
4. ✅ **Share URL**: Send to colleagues
5. ✅ **(Optional) Add Custom Domain**: Use your company domain
6. ✅ **(Optional) Add Authentication**: Restrict to company users only

---

## Support

- **Azure Static Web Apps Docs**: https://learn.microsoft.com/azure/static-web-apps/
- **GitHub Actions Logs**: Check repository Actions tab
- **Azure Support**: Available in Azure Portal

---

**Ready to deploy? Follow Option 1 (Azure Portal) for the easiest deployment!**
