# 0013: Lights

Decision record, 2026-11-03. [Scene conventions](../design/SCENE_CONVENTIONS.md#lights) specify the
result, and the [capability matrix](../reference/CAPABILITY_MATRIX.md) records current evidence.

## Context

`KHR_lights_punctual` defines directional, point and spot lights that shine along their node's local
-Z, as `UsdLuxDistantLight` and `UsdLuxShapingAPI` do. They differ in what a light is:

- glTF lights are punctual. A point or spot light's intensity is luminous intensity in candela, and a
  directional light's is illuminance in lux. UsdLux has no punctual light: its intensity is the
  luminance of an emitting surface in nits, so that an `intensity` of 1 makes a pixel of 1, unless
  `normalize` divides it by the light's size. `treatAsPoint` on a `SphereLight` is only a hint.
- glTF moves and turns a light with its node, but its properties do not scale with the node.
  UsdLux measures a light's area, and so its power when normalized, in world space.
- glTF's `range` is a hint that the light may be considered to reach zero there, and recommends a
  falloff that does. UsdLux has no cutoff.
- glTF recommends a spot falloff in the cosine of the angle, between the inner and outer cone
  angles. `UsdLuxShapingAPI` has a cone angle and a softness, a smoothstep in the angle that starts at
  `(1 - softness)` times the cone angle.

Storm, read in `HdStLight::_ApproximateAreaLight` and checked by the
[display check](#evidence-and-limits), draws a sphere light as a point at its translation, of
`intensity` times its projected area, `pi radius^2`, unless `normalize` is on, falling off with the
square of distance. It draws a distant light of `intensity` times its solid angle, which is 0 at an
angle of 0, unless `normalize` is on. It reads no shaping on a sphere light.

## Decision

### Placement

A light is a `Light_<index>` prim below each node that uses it, beside `Camera_<index>`, for the
same reason as [cameras](0012-cameras.md#placement).

### Units

A directional light is a `DistantLight` of angle 0 with `normalize` on, whose intensity UsdLux then
defines as illuminance in lux. The glTF value is authored unchanged.

For point and spot lights, the options were:

- A `SphereLight` with `normalize` on. Its intensity in candela is a quarter of `intensity`, at any
  radius, but Storm draws it four times as bright, because it treats a normalized intensity as
  candela.
- Empirical factors fitted to another renderer, as another glTF importer does. They have no meaning
  in UsdLux and would differ with each renderer.
- A `SphereLight` with `normalize` off, `treatAsPoint` on and a fixed small radius, of intensity
  `candela / (pi radius^2)`. UsdLux and Storm agree on this.

Choose the last. The radius is 1 millimeter, small against glTF assets, whose unit is the meter.
A sphere of uniform luminance lights a surface that sees all of it as a point of the same candela
does, so the radius matters only within a few millimeters of the light, and for the size of highlights
and shadow edges in renderers that ignore `treatAsPoint`. A smaller radius raises the authored
intensity, which for 1 millimeter stays within a float up to about 1e33 candela. Beyond that the
light is rejected.

### Transform

As with [cameras](0012-cameras.md#scale), the light prim has a transform that cancels its node's
scale and shear relative to the asset, so that UsdLux does not scale the sphere or distort the cone.
glTF gives a light the direction of the node's transform applied to -Z, which a scaled or sheared
node also defines. The frame keeps that axis and the plane of the node's Y, and is right-handed, so a
reflected node is accepted; a light shines the same either way. Only a node whose axes are zero or lie
in one plane is rejected, because a USD child cannot cancel a transform with no inverse.

### Spot cone

The cone angle is the outer cone angle. The softness, `1 - inner / outer`, starts the falloff at the
inner angle, as glTF does. The curves between them differ: glTF's recommendation reaches half
intensity nearer the inner angle. This is a stated approximation of a falloff that glTF itself only
recommends.

### Range

The range is ignored, with a warning naming the node and light, because UsdLux cannot express it.
glTF calls it a hint, and the light still falls off with the inverse square of distance, so a client
that ignores it shows the light further than the asset's author may have meant. A range on a
directional light has no meaning in glTF and is ignored without a warning.

## Evidence and limits

`test_lights` checks each light prim's world transform against the position and direction written
from the specification, and its intensity through the UsdLux definitions above. The
[sample check](0010-sample-baseline.md) does the same for every light of the selected scene of each
sample. The [display check](0007-display-check.md) renders a quad lit by one light at a time in Storm,
with no other light, and compares lights that glTF says give it the same illuminance.

Each change below was built into the plugin, and all three run against it. The sample column counts
the 12 files of the seven models whose selected scene has a light and that open: DirectionalLight,
GlamVelvetSofa, IridescenceSuzanne, LightsPunctualLamp, PointLightIntensityTest, TrafficCone and
NodePerformanceTest.

| Change to the plugin | Failing tests | Failed display comparisons | Sample files that differ |
| --- | --- | --- | --- |
| Candela authored as the sphere's intensity | `test_lights`, `test_light_failures_preserve_layer` | 10 | 6 |
| Distant light not normalized | `test_lights` | 2 | 6 |
| Scale not cancelled | `test_lights`, `test_lights_reference_into_a_scaled_stage` | 0 | 0 |
| Frame from the node's rotation without its shear | `test_lights` | 0 | 0 |
| Softness of `inner / outer` | `test_lights` | 0 | 0 |

Those samples have only directional and point lights, all under nodes without scale, so only the
fixtures find the last three. In the display check, the first change leaves the quad black, and the
second leaves the directional light's quad black.

Not established:

- Storm ignores a spot light's cone, so every spot light lights in all directions there. The display
  check states this, so that a change in Storm is seen.
- Other renderers than Storm were not run. One that ignores `treatAsPoint` draws a sphere of 1
  millimeter.
- Shadows were not checked.
- Animated lights are not supported, like all animation.
