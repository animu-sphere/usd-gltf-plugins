// SPDX-License-Identifier: Apache-2.0
// Reference path for check_samples.py. Use the upstream JavaScript implementation.
import {readFileSync} from 'node:fs';

const reference = process.env.USD_GLTF_MESHOPT_REFERENCE;
if (!reference) {
    throw new Error('Set USD_GLTF_MESHOPT_REFERENCE to meshopt_decoder_reference.js from meshoptimizer 0.25');
}
// Upstream's reference file uses ESM exports inside a CommonJS package.
const {MeshoptDecoder} = await import('data:text/javascript;base64,' + readFileSync(reference, 'base64'));
const requests = JSON.parse(readFileSync(0, 'utf8'));
const decoded = requests.map(({bytes, count, byteStride, mode, filter}) => {
    const target = Buffer.alloc(count * byteStride);
    MeshoptDecoder.decodeGltfBuffer(target, count, byteStride, Buffer.from(bytes, 'base64'), mode, filter);
    return target.toString('base64');
});
process.stdout.write(JSON.stringify(decoded));
