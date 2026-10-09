# Driver Call Locations

This document traces where OpenCL and OpenGL/GLES driver calls originate from in the codebase.

## OpenCL Driver Calls

### 1. System-Level OpenCL Utilities
**File**: `common/clutil.cc`

Low-level OpenCL driver calls for context and program management:

```cpp
// From clGetPlatformIDs() - Get available OpenCL platforms
clGetPlatformIDs(0, NULL, &num_platforms);

// From clGetDeviceIDs() - Get GPU device
clGetDeviceIDs(platform_ids[i], device_type, 1, &device_id, NULL);

// From clCreateContext() - Create OpenCL context
clCreateContext(NULL, 1, &device_id, NULL, NULL, &err);

// From clCreateProgramWithSource() - Load kernel source
clCreateProgramWithSource(ctx, 1, &csrc, NULL, &err);

// From clBuildProgram() - Compile kernel
clBuildProgram(prg, 1, &device_id, args, NULL, NULL);

// From clCreateKernel() - Create kernel instance
clCreateKernel(prg, "warpPerspective", &err);

// From clCreateBuffer() - Allocate GPU memory
clCreateBuffer(ctx, CL_MEM_READ_WRITE, 3*3*sizeof(float), NULL, &err);
```

### 2. Vision Processing (Model Transform)
**File**: `selfdrive/modeld/transforms/transform.cc`

OpenCL kernel execution for image transforms:

```cpp
// From clEnqueueWriteBuffer() - Upload transform matrix to GPU
clEnqueueWriteBuffer(q, s->m_y_cl, CL_TRUE, 0, 3*3*sizeof(float), 
                     (void*)projection_y.v, 0, NULL, NULL);

// From clSetKernelArg() - Set kernel arguments
clSetKernelArg(s->krnl, 0, sizeof(cl_mem), &in_yuv);
clSetKernelArg(s->krnl, 1, sizeof(cl_int), &in_stride);
// ... more args

// From clEnqueueNDRangeKernel() - Execute kernel on GPU
clEnqueueNDRangeKernel(q, s->krnl, 2, NULL, 
                       (const size_t*)&work_size_y, NULL, 0, 0, NULL);
```

**Kernel File**: `selfdrive/modeld/transforms/transform.cl`
```opencl
__kernel void warpPerspective(...)
```

### 3. Vision Buffer Management
**File**: `msgq_repo/msgq/visionipc/visionbuf_cl.cc`

OpenCL buffer operations for zero-copy camera frames:

```cpp
// From clCreateBuffer() - Create buffer from DMA-BUF fd
clCreateBuffer(ctx, CL_MEM_READ_WRITE | CL_MEM_EXT_PTR_XILINX, 
               len, &mem_ext, &err);

// From clEnqueueCopyBuffer() - Copy between buffers
clEnqueueCopyBuffer(cmd_queue, src, dst, 0, 0, size, 0, NULL, NULL);

// From clReleaseMemObject() - Free GPU memory
clReleaseMemObject(mem);
```

### 4. YUV Loading
**File**: `selfdrive/modeld/transforms/loadyuv.cc`

OpenCL for YUV to planar conversion:

```cpp
// Similar pattern: clSetKernelArg + clEnqueueNDRangeKernel
clEnqueueNDRangeKernel(q, s->krnl, 2, NULL, 
                       (const size_t*)&work_size, NULL, 0, 0, NULL);
```

**Kernel File**: `selfdrive/modeld/transforms/loadyuv.cl`

### 5. ACL (ARM Compute Library)
**Files**: `third_party/arm_compute/src/runtime/CL/*.cpp`

ACL wraps OpenCL calls internally:

```cpp
// From CLKernelLibrary.cpp - ACL's internal OpenCL wrapper
clCreateCommandQueue(context, device, properties, &err);
clEnqueueMapBuffer(queue, buffer, blocking, flags, offset, size, ...);
clEnqueueUnmapMemObject(queue, buffer, ptr, 0, NULL, NULL);
```

ACL provides higher-level abstractions:
- `CLTensor` - GPU tensor management
- `CLKernelLibrary` - Kernel compilation cache
- `CLScheduler` - Command queue management

## OpenGL/GLES Driver Calls

### 1. UI Camera View Rendering
**File**: `selfdrive/ui/qt/widgets/cameraview.cc`

OpenGL ES for camera display:

```cpp
// From initializeGL() - Setup shaders
initializeOpenGLFunctions();
program = std::make_unique<QOpenGLShaderProgram>(context);
program->addShaderFromSourceCode(QOpenGLShader::Vertex, frame_vertex_shader);
program->addShaderFromSourceCode(QOpenGLShader::Fragment, frame_fragment_shader);
program->link();

// From paintGL() - Render frame
glClearColor(bg.redF(), bg.greenF(), bg.blueF(), bg.alphaF());
glClear(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT);
glViewport(0, 0, glWidth(), glHeight());
glBindVertexArray(frame_vao);
glUseProgram(program->programId());

// Upload texture data
glActiveTexture(GL_TEXTURE0);
glBindTexture(GL_TEXTURE_2D, textures[0]);
glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, stream_width, stream_height, 
                GL_RED, GL_UNSIGNED_BYTE, frame->y);

// Draw
glUniformMatrix4fv(program->uniformLocation("uTransform"), 1, GL_TRUE, frame_mat.v);
glEnableVertexAttribArray(0);
glDrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_BYTE, (const void *)0);
```

### 2. EGL for Buffer Sharing
**File**: `selfdrive/ui/qt/widgets/cameraview.cc` (QCOM2 path)

EGL for zero-copy camera buffer import:

```cpp
// From vipcConnected() - Import DMA-BUF into OpenGL
EGLDisplay egl_display = eglGetCurrentDisplay();
EGLint img_attrs[] = {
  EGL_WIDTH, width,
  EGL_HEIGHT, height,
  EGL_LINUX_DRM_FOURCC_EXT, fourcc,
  EGL_DMA_BUF_PLANE0_FD_EXT, fd,
  EGL_DMA_BUF_PLANE0_OFFSET_EXT, offset,
  EGL_DMA_BUF_PLANE0_PITCH_EXT, pitch,
  EGL_NONE
};
EGLImageKHR image = eglCreateImageKHR(egl_display, EGL_NO_CONTEXT, 
                                       EGL_LINUX_DMA_BUF_EXT, NULL, img_attrs);

// From paintGL() - Use imported buffer as texture
glEGLImageTargetTexture2DOES(GL_TEXTURE_EXTERNAL_OES, egl_images[frame->idx]);
```

### 3. Qt OpenGL Wrapper
**Files**: `selfdrive/ui/qt/*.cc`

Qt's QOpenGL* classes wrap OpenGL:

```cpp
// QOpenGLShaderProgram wraps glCreateShader, glCompileShader, glLinkProgram
// QOpenGLBuffer wraps glGenBuffers, glBindBuffer, glBufferData
// QOpenGLVertexArrayObject wraps glGenVertexArrays, glBindVertexArray
```

## Driver Stack

### OpenCL Call Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    User Code                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ modeld      │  │ stereod     │  │ common/clutil.cc    │ │
│  │ transforms  │  │ ACL SGM     │  │ context management  │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
└─────────┼────────────────┼────────────────────┼────────────┘
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│              libOpenCL.so (ICD Loader)                      │
│         (Installable Client Driver - dispatches to)         │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│           libmali.so (ARM Mali GPU Driver)                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Mali GPU Kernel Driver (mali_kbase.ko in kernel)     │  │
│  │  - Job scheduling                                     │  │
│  │  - Memory management                                  │  │
│  │  - Power management                                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│              Mali G610/G52 Hardware                         │
└─────────────────────────────────────────────────────────────┘
```

### OpenGL ES Call Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    User Code                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  selfdrive/ui/qt/widgets/cameraview.cc                │  │
│  │  - QOpenGLShaderProgram                               │  │
│  │  - glDrawElements                                     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│              Qt OpenGL Wrapper (Qt5)                        │
│         (QOpenGLFunctions, QOpenGLShaderProgram)            │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│           libGLESv2.so / libEGL.so                          │
│         (Mali GPU Userspace Driver)                         │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│           libmali.so (Same driver, different API)           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Mali GPU Kernel Driver (mali_kbase.ko)               │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│              Mali G610/G52 Hardware                         │
└─────────────────────────────────────────────────────────────┘
```

## Key Driver Files

### System Drivers
| File | Purpose |
|------|---------|
| `/usr/lib/libOpenCL.so` | OpenCL ICD loader |
| `/usr/lib/libmali.so` | Mali GPU driver (OpenCL + OpenGL) |
| `/usr/lib/libGLESv2.so` | OpenGL ES 2.0/3.0 |
| `/usr/lib/libEGL.so` | EGL windowing |
| `/sys/class/misc/mali0/device/clock` | GPU frequency |

### Kernel Module
| File | Purpose |
|------|---------|
| `/sys/module/mali_kbase/` | Mali kernel driver params |
| `/sys/class/misc/mali0/` | GPU device interface |

## Summary

| Component | Driver API | Key Files | Purpose |
|-----------|-----------|-----------|---------|
| modeld | OpenCL | `transform.cc`, `loadyuv.cc` | Image preprocessing |
| stereod | OpenCL (via ACL) | `sgm.py` → ACL → `libmali.so` | Stereo depth |
| ui | OpenGL ES | `cameraview.cc` | Camera display |
| Both | EGL | `cameraview.cc` | Buffer sharing |

Both OpenCL and OpenGL ES go through the same `libmali.so` driver, which manages the Mali GPU hardware and schedules work from both APIs.
