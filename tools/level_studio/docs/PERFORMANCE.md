# Performance in 2.0.0

The viewport requests a compact geometry transport. Large vertex/UV/color arrays travel as little-endian float32 buffers and triangle indices as uint32 buffers, rather than millions of decimal JSON values. This uses the same float32 precision already consumed by WebGL; source files, serialized edit parameters and project coordinates retain their original values.

Parsed levels and prepared scene state are cached in memory. Numeric buffers are copied in bulk while scene metadata stays isolated. Repeated static module transfers reuse compressed bytes and support ETags. Preview imports avoid reparsing the whole original level. Native replacements revalidate and reparse the affected resource.

## Measured example

Measurements on the development Windows machine with the owner's European dump are examples, not speed guarantees:

| Operation | Previous path | 2.0.0 |
| --- | ---: | ---: |
| Town scene JSON before HTTP compression | 255.2 MB | 113.8 MB |
| Town response preparation and gzip | 11.97 s | 4.73 s |
| Repeated Town scene retrieval/copy | 8.24 s | 4.54 s |
| Repeated Training Room scene retrieval | 0.417 s | 0.165 s |

The Town payload is about **55% smaller** before HTTP compression. The first Town decode still takes roughly **38 seconds** in this measurement. Character-library initialization, texture decoding, GPU upload and shader compilation can add time on a first visit. Only two decoded levels are kept resident to limit memory use.

Large levels remain demanding; the editor does not promise instant startup or a fixed frame rate. CPU, GPU, display resolution, source size and security scanning affect observed performance.
