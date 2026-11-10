# 0024: Import measurements and mesh sharing

Decision record, 2026-11-09. Current measurements and sample outcomes belong to the
[capability matrix](../reference/CAPABILITY_MATRIX.md#import-measurements).

## Context

The current plan asks for load and mesh duplication measurements before taking on
sharing. The sample check's timeout for NodePerformanceTest's GLB combines opening
the stage with reading its values and images. Those are different operations:
authoring an asset-valued shader input does not itself read its image. An import
cache on the authoring thread therefore does not address that timeout.

## Decision

Measure `Usd.Stage.Open` in fresh processes, serially, without rendering. Record
each run's time and peak resident memory before traversing the stage, together
with runtime information and source and plugin hashes. Do not flush OS caches;
these are exploratory measurements, not cold disk or isolated CPU benchmarks.
Peak resident memory includes Python and USD libraries, and is not an allocation
count attributable to the importer.

After timing, count mesh occurrences, point occurrences and point/topology array
bytes. Group exact matches of points, face counts and face indices with SHA-256,
including instance proxies so counts describe scene occurrences. The unique byte
count describes only this subset of mesh data: it excludes normals, UVs,
other primvars, materials, transforms and USD overhead. It is not a prediction of
RSS saved by sharing. Tests use two occurrences of a mesh with different
primitives so both repeated and distinct arrays must be counted correctly.

Keep the current duplicated meshes. NodePerformanceTest has many nodes, but its
points/topology do not repeat. ABeautifulGame does have repeated arrays, so it
is useful for later sharing experiments. Sharing needs an identity and lifetime
contract and composition tests for per-node materials, transforms and primvars;
the measurements alone do not justify that additional machinery for this release.
The [backlog](../roadmap/backlog.md) retains optimization and sharing work.

Hold `Ar.ResolverScopedCache` around the sample check's meaning comparison. Its
image reads run on that thread and can use the package cache that already exists.
End the scope before preparing render sessions. This adds no persistent image or
parsed-source cache to the importer, and keeps file changes visible outside a
caller's scope, as [ADR 0001](0001-image-assets.md#lifetime-cache-and-concurrency)
requires. A checker timeout must not be described as a failed `Stage.Open` without
measuring that operation separately.

## Image read measurements

After stage opening and geometry inspection, resolve the shader file inputs in
one `Ar.ResolverScopedCache`. Open each distinct resolved path once per pass,
twice in the same scope, so the second pass can detect reuse. Both shader paths
can refer to the same image. Deduplication keeps the workload independent of how
many shader nodes refer to it. Report the wall time of each pass separately from
`Stage.Open`.

Enable OpenUSD tracing during the passes and count the calls into KTX2-to-PNG
conversion, with their total elapsed time. This includes transcoding and PNG
encoding inside the converter. Source resolution, image header checks outside
the converter, and the opening of PNG, JPEG or WebP images contribute to the
pass time without counting as KTX2 conversions.

## Limits

No throughput target, mesh instancing, lazy loading or long-lived cache is adopted.
The image passes measure a serial resolver workload. Renderer loading and
display compatibility need separate checks.
