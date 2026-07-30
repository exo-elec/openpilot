/*
 * 3D Point Cloud Reprojection — OpenCL Kernel
 * 
 * Reprojects a depth map to 3D points using camera intrinsics.
 * One work-item per pixel. Optimized for Mali G52 MC3.
 * 
 * Build on target:
 *   clBuildProgram(ctx, device, "reproject.cl", "-cl-fast-relaxed-math")
 */

__kernel void reproject_depth(
    __global const float* depth,      // H x W depth values (meters)
    __global const float* x_norm,     // H x W (u - cx) / fx
    __global const float* y_norm,     // H x W (v - cy) / fy
    __global float* xyz,              // H x W x 3 output points
    const int width,
    const int height,
    const float min_depth,            // Filter near noise
    const float max_depth             // Filter far invalid
)
{
    int gid = get_global_id(0);
    int total = width * height;
    
    if (gid >= total) return;
    
    float z = depth[gid];
    
    // Output index (interleaved xyz)
    int out_idx = gid * 3;
    
    // Filter invalid depth
    if (z < min_depth || z > max_depth || z <= 0.0f) {
        xyz[out_idx + 0] = 0.0f;
        xyz[out_idx + 1] = 0.0f;
        xyz[out_idx + 2] = 0.0f;
        return;
    }
    
    float x = x_norm[gid] * z;
    float y = y_norm[gid] * z;
    
    xyz[out_idx + 0] = x;
    xyz[out_idx + 1] = y;
    xyz[out_idx + 2] = z;
}

/*
 * Transform 3D points with 4x4 matrix
 * One work-item per point. Used for coordinate frame transforms.
 */
__kernel void transform_points(
    __global const float* points_in,  // N x 3 (interleaved)
    __global float* points_out,       // N x 3 (interleaved)
    __constant float* transform,      // 4 x 4 matrix (row-major)
    const int n_points
)
{
    int gid = get_global_id(0);
    if (gid >= n_points) return;
    
    int idx = gid * 3;
    float x = points_in[idx + 0];
    float y = points_in[idx + 1];
    float z = points_in[idx + 2];
    
    // 4x4 matrix multiply (assume w=1)
    float ox = transform[0]*x + transform[1]*y + transform[2]*z + transform[3];
    float oy = transform[4]*x + transform[5]*y + transform[6]*z + transform[7];
    float oz = transform[8]*x + transform[9]*y + transform[10]*z + transform[11];
    float ow = transform[12]*x + transform[13]*y + transform[14]*z + transform[15];
    
    float inv_w = 1.0f / ow;
    points_out[idx + 0] = ox * inv_w;
    points_out[idx + 1] = oy * inv_w;
    points_out[idx + 2] = oz * inv_w;
}
