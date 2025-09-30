# Brainlife Igneous Meshing App

This Brainlife app runs [igneous](https://github.com/seung-lab/igneous) meshing tasks on a **precomputed segmentation layer**. It generates 3D meshes suitable for visualization and analysis, with optional **multiresolution outputs** (levels of detail).

---

## Features
- Runs igneous meshing on local (`file://`) or cloud (`s3://`, `gs://`, etc.) datasets  
- Supports simplification, dust filtering, spatial indexing, and shard output  
- Optional **multiresolution meshes** with adjustable LODs, quantization, and chunk size  
- Compatible with Brainlife `config.json` interface  

---