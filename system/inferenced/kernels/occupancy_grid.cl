/*
 * Occupancy Grid Fusion — OpenCL Kernel
 * 
 * Fuses 3D point cloud with segmentation mask into a 2D bird's-eye-view
 * occupancy grid. One work-item per grid cell. Mali G52 MC3 optimized.
 */

__kernel void fuse_occupancy_grid(
    __global const float* xyz,        // N x 3 points (interleaved, vehicle frame)
    __global const uchar* labels,     // N labels per point (0=free, 1=occupied, 2=unknown)
    __global float* grid,             // H x W grid (row-major)
    __global int* hit_count,          // H x W hit counter
    const int grid_width,
    const int grid_height,
    const float grid_resolution,      // meters per cell
    const float grid_origin_x,        // grid center x in vehicle frame
    const float grid_origin_y,        // grid center y in vehicle frame
    const float min_height,           // filter ground points
    const float max_height,           // filter overhang
    const int n_points
)
{
    int gid = get_global_id(0);
    if (gid >= n_points) return;
    
    int idx = gid * 3;
    float x = xyz[idx + 0];  // forward
    float y = xyz[idx + 1];  // left
    float z = xyz[idx + 2];  // up
    
    // Height filter
    if (z < min_height || z > max_height) return;
    
    // Map to grid coordinates
    float gx = (x - grid_origin_x) / grid_resolution;
    float gy = (y - grid_origin_y) / grid_resolution;
    
    int col = (int)(gx + 0.5f);
    int row = (int)(gy + 0.5f);
    
    // Bounds check
    if (row < 0 || row >= grid_height || col < 0 || col >= grid_width) return;
    
    int grid_idx = row * grid_width + col;
    
    // Update cell using atomic operations for thread safety
    uchar label = labels[gid];
    
    if (label == 1) {  // occupied
        atomic_add(&hit_count[grid_idx], 1);
        // Exponential moving average for occupancy probability
        float current = grid[grid_idx];
        grid[grid_idx] = fma(0.1f, 1.0f, fma(-0.1f, current, current));
    } else if (label == 0) {  // free
        atomic_add(&hit_count[grid_idx], 1);
        float current = grid[grid_idx];
        grid[grid_idx] = fma(0.05f, 0.0f, fma(-0.05f, current, current));
    }
}

/*
 * Apply temporal decay to grid
 * One work-item per cell. Run each frame before fusion.
 */
__kernel void decay_grid(
    __global float* grid,
    __global int* hit_count,
    const int grid_size,
    const float decay_rate          // e.g., 0.95 per frame
)
{
    int gid = get_global_id(0);
    if (gid >= grid_size) return;
    
    grid[gid] *= decay_rate;
    hit_count[gid] = 0;
}
