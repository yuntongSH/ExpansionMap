# Isochrone API Fix - October 17, 2025

## Problem Identified

The OpenRouteService isochrone API was not working due to incorrect authentication method.

### Root Cause
1. **Wrong Authentication Method**: API key was being passed as a URL query parameter (`?api_key=...`)
2. **Correct Method**: OpenRouteService API requires the key in the `Authorization` header
3. **CORS Issues**: When opening HTML directly from file system (file:// protocol), CORS blocks external API calls

## Solution Implemented

### Two-Tier Approach

**Primary Method: Direct API Call**
- Uses proper `Authorization` header with API key
- Works when the HTML is served via HTTP/HTTPS (e.g., from a web server)
- Fastest and most reliable method

```javascript
const response = await fetch(apiUrl, {
    method: 'POST',
    headers: {
        'Authorization': ORS_API_KEY,  // ← Fixed!
        'Content-Type': 'application/json',
        'Accept': 'application/json, application/geo+json'
    },
    body: JSON.stringify(body)
});
```

**Fallback Method: CORS Proxy**
- Automatically activates if direct call fails (CORS error)
- Uses corsproxy.io to bypass CORS restrictions
- Allows the map to work even when opened as a local file (file://)
- API key passed as query parameter when using proxy

```javascript
if (error.name === 'TypeError' && error.message.includes('Failed to fetch')) {
    console.warn(`⚠️ Direct API call failed (likely CORS), trying with proxy...`);
    return await fetchWithProxy(apiUrl, body);
}
```

## Changes Made

### 1. Updated `fetchTruckIsochronesMultiple()` Function
- **Before**: Always used CORS proxy with API key in URL
- **After**: Tries direct API call first, falls back to proxy if needed

### 2. Added `fetchWithProxy()` Function
- New fallback function for CORS-restricted environments
- Handles proxy-specific error messages
- Maintains same functionality as direct call

### 3. Better Error Handling
- Distinguishes between CORS errors and API errors
- Provides helpful console messages
- Automatic fallback without user intervention

## Testing

### Test File Created
- `test_isochrone.html` - Standalone test page
- Tests Paris, France (48.8566°N, 2.3522°E)
- Fetches 30-minute and 60-minute isochrones
- Shows detailed API response information

### How to Test
1. Open `BioCO2 Expansion Map 2025.html` in browser
2. Click on any site marker
3. Click the "Show Isochrone Area" toggle
4. Purple isochrone zones should appear (30min, 1h, 2h)

## Expected Behavior

### When Served via HTTP/HTTPS
- Uses direct API call (faster)
- Console shows: "📥 Response status: 200 OK"

### When Opened as File (file://)
- Direct call fails (CORS)
- Console shows: "⚠️ Direct API call failed (likely CORS), trying with proxy..."
- Falls back to proxy automatically
- Console shows: "✅ Isochrones via proxy"

## OpenRouteService API Details

- **Endpoint**: `https://api.openrouteservice.org/v2/isochrones/driving-hgv`
- **Profile**: Heavy Goods Vehicle (driving-hgv)
- **Method**: POST
- **Authentication**: Authorization header
- **Rate Limits**: 40 requests per minute (free tier)
- **Timeouts**: 30 minutes max, 60 minutes max (API limit 1 hour)

## Future Improvements

1. **Cache Isochrones**: Store fetched isochrones to avoid repeated API calls
2. **Progressive Loading**: Show partial results while fetching
3. **Alternative APIs**: GraphHopper or Mapbox as backup
4. **Self-hosted**: Deploy own routing engine for unlimited requests

## Files Modified

- `generate_map.py` - Updated isochrone fetch logic
- `BioCO2 Expansion Map 2025.html` - Regenerated with fixes
- `test_isochrone.html` - New test file (created)
- `ISOCHRONE_FIX.md` - This documentation

## Commit Message

```
Fix OpenRouteService isochrone API authentication
- Changed from URL query parameter to Authorization header
- Added automatic CORS proxy fallback for file:// protocol
- Improved error handling and console logging
- Maintains backward compatibility
- Created test_isochrone.html for API testing
```
