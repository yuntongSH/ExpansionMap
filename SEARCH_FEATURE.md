# Operator Search Feature

## Overview
A new search bar has been added to the BioCO2 Expansion Map to help users quickly find and identify sites by operator name.

## Location
The search bar is positioned at the top of the Legend & Filters panel (left side of the map).

## Features

### 1. **Partial Name Matching**
- Users can enter partial operator names (e.g., "Agri" to find "Agricola Italiana")
- Search is case-insensitive for better usability
- Supports any part of the operator name

### 2. **Real-time Results**
- Search results update automatically as you type (300ms debounce for performance)
- Shows number of matching sites and operators found
- Example: "✅ Found 45 site(s) from 3 operator(s)"

### 3. **Visual Feedback**
- Matching sites appear on the map with a pulse animation
- Non-matching sites are automatically hidden
- Blue-themed search container for clear visibility

### 4. **Auto-zoom to Results**
- Map automatically zooms to fit all matching sites
- Padding around results for better viewing

### 5. **Easy Clear**
- Clear search by deleting text or pressing **Escape** key
- Returns to normal filter mode when search is cleared

## How to Use

1. **Basic Search**: Type an operator name (or part of it) in the search box
   - Example: Type "Biogas" to see all Biogas operators

2. **Partial Search**: Type just a few letters to narrow down
   - Example: Type "Agri" to find agricultural operators

3. **Clear Search**: 
   - Delete all text, or
   - Press the **Escape** key

4. **View Results**: 
   - Green message = sites found
   - Red message = no matches

## Examples

| Search Query | What It Finds |
|--------------|---------------|
| "Shell" | All Shell-operated sites |
| "Agr" | Agricultural operators (Agricola, Agriculture, etc.) |
| "Bio" | Operators with "Bio" in their name (Biogas, Biomethane, etc.) |
| "energia" | Energy companies with "energia" in the name |
| "N/A" | Sites with no operator information |

## Technical Details

- **Total Operators**: 5,283 unique operators in the database
- **Search Method**: Client-side JavaScript filtering
- **Performance**: Debounced input (300ms) for smooth typing
- **Data Source**: `operator` field from map.csv

## Notes

- Search overrides normal techno/status filters while active
- Empty search returns to normal filtering behavior
- Case-insensitive matching ensures better results
- Search works on all 7,938+ sites in the database
