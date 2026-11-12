// SPDX-License-Identifier: Apache-2.0
// Reference path for check_samples.py. It reads Draco through Google's WebAssembly API.
const fs = require('node:fs');
const path = require('node:path');

async function main() {
    const modulePath = process.env.USD_GLTF_DRACO_REFERENCE;
    const {createDecoderModule} = require(modulePath ? path.resolve(modulePath) : 'draco3dgltf');
    const draco = await createDecoderModule({});
    const decoder = new draco.Decoder();
    const requests = JSON.parse(fs.readFileSync(0, 'utf8'));
    const types = {
        5120: draco.DT_INT8, 5121: draco.DT_UINT8,
        5122: draco.DT_INT16, 5123: draco.DT_UINT16,
        5125: draco.DT_UINT32, 5126: draco.DT_FLOAT32,
    };

    function readBytes(size, fill) {
        const pointer = draco._malloc(size);
        try {
            if (!pointer || !fill(pointer)) {
                throw new Error('Could not read Draco output');
            }
            return Buffer.from(draco.HEAPU8.subarray(pointer, pointer + size)).toString('base64');
        } finally {
            draco._free(pointer);
        }
    }

    const meshes = requests.map(({bytes, attributes}) => {
        const input = Buffer.from(bytes, 'base64');
        const mesh = new draco.Mesh();
        try {
            const status = decoder.DecodeArrayToMesh(input, input.length, mesh);
            if (!status.ok()) {
                throw new Error(status.error_msg());
            }
            const count = mesh.num_points();
            const indexSize = mesh.num_faces() * 3 * Uint32Array.BYTES_PER_ELEMENT;
            const indices = readBytes(indexSize, ptr => decoder.GetTrianglesUInt32Array(mesh, indexSize, ptr));
            const values = attributes.map(({id, component, size}) => {
                const attr = decoder.GetAttributeByUniqueId(mesh, id);
                const length = count * size;
                return readBytes(length, ptr => decoder.GetAttributeDataArrayForAllPoints(
                    mesh, attr, types[component], length, ptr));
            });
            return {indices, values};
        } finally {
            draco.destroy(mesh);
        }
    });
    draco.destroy(decoder);
    process.stdout.write(JSON.stringify(meshes));
}

main().catch(error => {
    console.error(error.message);
    process.exitCode = 1;
});
