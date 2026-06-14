"""
Updates the upstream (UPELEV) and Downstream(ELEVDOWN) elevations to the SdGravityMain
based on the nearest manhole/inlet invert elevations so the slope of the pipe can be calculated
Process:    1. Create start and end points for each gravity main pipe
            2. Join those points with manholes/inlets to grab invert elevations using NEAR tool
            3. Write those elevations back to the SdGravityMain 'ELEVUP' & 'ELEVDOWN' fields
            4. Lastly, the slope of each gravity main is calculated and written to the 'Slope' field
"""

import arcpy # for ArcGIS operations
import datetime # For logging date and time to text file

# SDE Path:
sde_path = r"....\Storm.sde" # Full path removed for github repository.

# Set environment variables:
arcpy.env.workspace = sde_path # Set the workspace to the geodatabase connection
arcpy.env.qualifiedFieldNames = False # Use simple field names without table prefixes (e.g., "INV" instead of "sdManhole.INV")
arcpy.env.overwriteOutput = True # Allow overwriting of existing output datasets
edit = arcpy.da.Editor(sde_path) # Create an edit session object for making edits to the sde database

# Log Path:
log_path = r"....\gravity_main_slope\storm_gravity_main_slope_log.txt" # Full path removed for github repository.

# Function to write to the log file everytime script runs:
def writeToLog(message):
    file = open(log_path, "a")
    # Write timestamp + message
    timestamp = datetime.datetime.now()
    file.write(f"{timestamp} - {message}")
    file.close()

# Input layers:
sdgravity_main = "DBO.sdGravityMain" # Gravity pipe lines
sdmanhole = "DBO.sdManhole" # Manhole point features
sdinlet = "DBO.sdInlet" # Inlet point features

# ----------------------------------------------------------
# Step 1: Create temporary start and end points in memory:
# ----------------------------------------------------------

print("\nStarting elevation update process...")

# Define temporary in-memory feature classes to store the start and end vertices of each pipe:
start_points = "in_memory\\start_points"
end_points = "in_memory\\end_points"

print("Creating start and end points on sdGravityMain segments...")

# Extract the START vertex of each gravity main line and create a point feature:
arcpy.management.FeatureVerticesToPoints(sdgravity_main, start_points, "START") # The start point is the first vertex in the line geometry.
# Extract the END vertex of each gravity main line and create a point feature:
arcpy.management.FeatureVerticesToPoints(sdgravity_main, end_points, "END") # The end point is the first vertex in the line geometry. Reversing line direction will swap these (which is what we want).
# (ORIG_FID field will store the original pipe's OBJECTID used later)

# Count and print number of all start and end points created:
start_count = int(arcpy.management.GetCount(start_points)[0])
end_count = int(arcpy.management.GetCount(end_points)[0])
print(f"  Start points created: {start_count}")
print(f"  End points created:   {end_count}")

# --------------------------------------------------------------------------------------------
# Step 2: Create temporary merged feature layer including both manhole and inlet elevations:
# --------------------------------------------------------------------------------------------

print("\nCreating a temporary merged feature layer of all sdManhole and sdInlet nodes...")

# Merge manholes and inlets into a single merged feature class stored in memory:
merged_storm_nodes = "in_memory\\merged_storm_nodes"

# Merge the two feature classes using Copy:
print("Combining manholes and inlets using Copy + Append...")
arcpy.CopyFeatures_management(sdmanhole, merged_storm_nodes)
arcpy.Append_management(sdinlet, merged_storm_nodes, schema_type="NO_TEST")

# Verify the merge produced records:
merged_count = int(arcpy.management.GetCount(merged_storm_nodes)[0])
print(f"  Merged storm nodes count: {merged_count}")

# Apply data filters to remove invalid/unrealistic elevation values:
print("\nApplying filters to storm nodes...")
arcpy.MakeFeatureLayer_management(
    merged_storm_nodes,             # Input: the merged features we just created
    "filtered_nodes",               # Output: name of the new filtered layer
                                    # Keep if ALL the following is true:
    "INV IS NOT NULL "                  # - Invert elevation exists
    "AND RIMELEV IS NOT NULL "          # - AND RIM elevation exists
    "AND RIMELEV < 6000 "               # - AND RIM elevation is below 6000ft
)

# --------------------------------------------
# Step 3: Run NEAR tool on start/end points:
# --------------------------------------------

print("\nRunning NEAR analysis...")

# Find the nearest storm node (manhole/inlet) to each start point within 3 feet:
arcpy.Near_analysis(start_points, "filtered_nodes", search_radius = "3 FEET")
# Find the nearest storm node (manhole/inlet) to each end point within 3 feet:
arcpy.Near_analysis(end_points, "filtered_nodes", search_radius = "3 FEET")

# --------------------------------------------------------
# Step 4: Create lookup dictionaries to store elevations:
# --------------------------------------------------------

print("\nBuilding elevation lookup dictionaries...")

# Build a single lookup for node invert elevations: {node_OBJECTID: INV}
inv_lookup = {
    node_oid: inv
    for node_oid, inv in arcpy.da.SearchCursor("filtered_nodes", ["OBJECTID", "INV"])
}

# Create dictionary to store upstream invert elevations: {pipe_OBJECTID: invert_elevation}
# Example of one entry: {12345: 4500.5} where 12345 is the OBJECTID of the pipe and 4500.5 is the upstream elevation
start_dict = {}

# Iterate through all start points (UPSTREAM):
with arcpy.da.SearchCursor(start_points, ["ORIG_FID", "NEAR_FID"]) as rows: #ORIG_FID is the original pipe OBJECTID, NEAR_FID is the matched node OBJECTID
    for oid, near_fid in rows:
        if near_fid != -1: # -1 means no match found (no node within 3 feet)
            inv = inv_lookup.get(near_fid)
            if inv is not None:
                # Store the upstream invert elevation:
                start_dict[oid] = inv

# Create dictionary to store downstream invert elevations: {pipe_OBJECTID: invert_elevation}
# Example of one entry: {12345: 4500.5} where 12345 is the OBJECTID of the pipe and 4500.5 is the downstream elevation

end_dict = {}

# Iterate through all end points (DOWNSTREAM):
with arcpy.da.SearchCursor(end_points, ["ORIG_FID", "NEAR_FID"]) as rows: #ORIG_FID is the original pipe OBJECTID, NEAR_FID is the matched node OBJECTID
    for oid, near_fid in rows:
        if near_fid != -1: # -1 means no match found (no node within 3 feet)
            inv = inv_lookup.get(near_fid)
            if inv is not None:
                # Store the downstream invert elevation:
                end_dict[oid] = inv

# Print number of start and end elevations collected:
print(f"  Start elevations collected: {len(start_dict)}")
print(f"  End elevations collected:   {len(end_dict)}")

# ---------------------------------------------------------------
# Step 5: Update sdGravityMain layer with new elevation values
# ---------------------------------------------------------------

print("\nUpdating sdGravityMain with new elevation values...")
edit.startEditing(with_undo=False, multiuser_mode=True) # Start an edit session on the sde database
edit.startOperation() # Begin an edit operation

# Create counters to track how many records were updated vs skipped
updated_count = 0
skipped_count = 0

# Open an update cursor on the gravity main feature class:
with arcpy.da.UpdateCursor(sdgravity_main, ["OBJECTID", "ELEVUP", "ELEVDOWN"]) as cur:
    # Iterate through every gravity main pipe
    for oid, old_up, old_down in cur:
        # Look up the new upstream elevation from our start_dict:
        new_up_elevation = start_dict.get(oid)
        # Look up the new downstream elevation from our end_dict:
        new_down_elevation = end_dict.get(oid)

        # Skip if we couldn't find elevations for either end:
        if new_up_elevation is None or new_down_elevation is None:
            skipped_count += 1
            continue

        # Skip if elevations haven't changed:
        if new_up_elevation == old_up and new_down_elevation == old_down:
            skipped_count += 1
            continue

        # Update the row with the new elevation values:
        cur.updateRow((oid, new_up_elevation, new_down_elevation))
        updated_count += 1

# Stop editing session:
edit.stopOperation()
edit.stopEditing(save_changes=True)

# ---------------------------------------------------------------
# Step 6: Calculate and write Slope values to sdGravityMain
# ---------------------------------------------------------------

print("\nCalculating slope values and writing to Slope field...")
edit.startEditing(with_undo=False, multiuser_mode=True)
edit.startOperation()

slope_updated = 0
slope_skipped = 0

with arcpy.da.UpdateCursor(sdgravity_main, ["OBJECTID", "ELEVUP", "ELEVDOWN", "SHAPE@LENGTH", "Slope"]) as cur:
    for oid, elev_up, elev_down, length, old_slope in cur:

        # Skip if elevations or length are missing/zero
        if elev_up is None or elev_down is None or length is None or length == 0:
            slope_skipped += 1
            continue

        # Calculate slope as a decimal: (rise / run) 
        new_slope = ((elev_up - elev_down) / length)

        # Skip if value hasn't changed (round to avoid float noise)
        if old_slope is not None and round(new_slope, 6) == round(old_slope, 6):
            slope_skipped += 1
            continue

        cur.updateRow((oid, elev_up, elev_down, length, new_slope))
        slope_updated += 1

edit.stopOperation()
edit.stopEditing(save_changes=True)

print(f"  Slope values written:  {slope_updated}")
print(f"  Slope values skipped:  {slope_skipped}")

# --------------------------------------------------
# Step 7: (Optional) Calculate negative slope stats
# --------------------------------------------------

negative_slope_count = 0
total_valid_pipes = 0

with arcpy.da.SearchCursor(sdgravity_main, ["ELEVUP", "ELEVDOWN"]) as cur:
    for up, down in cur:

        # Only count pipes that have both elevations populated
        if up is not None and down is not None:
            total_valid_pipes += 1

            if up < down:
                negative_slope_count += 1

# Calculate percentage safely
if total_valid_pipes > 0:
    negative_slope_percent = (negative_slope_count / total_valid_pipes) * 100
else:
    negative_slope_percent = 0

# ---------------------------------
# Step 8: (Optional) Print results
# ---------------------------------

# Write results to log file:
if updated_count > 0:
    writeToLog(f"Storm gravity main elevations updated successfully.\n")
    writeToLog(f"Records updated: {updated_count}\n")
    writeToLog(f"Negative slopes: {negative_slope_count} ({negative_slope_percent:.2f}%)\n\n")
else:
    writeToLog("No storm gravity main elevation updates needed.\n\n")

# Print summary:
print(f"\n✅ Storm gravity main elevations updated successfully!")
print(f"   Updated features: {updated_count}")
print(f"   Skipped (no change or no data): {skipped_count}")
print(f"   Total processed: {updated_count + skipped_count}")
print(f"   Negative slopes: {negative_slope_count} ({negative_slope_percent:.2f}%)")