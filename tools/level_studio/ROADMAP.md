# Roadmap after 2.1.1

Version **2.0.0** includes persisted model import/replacement/duplication and PNG texture import/replacement. Validated compatible replacements can be written to existing game resources. New models, duplicated scene instances and new texture allocation remain explicitly labelled project previews.

Version **2.1.0** adds native level creation from source templates, reversible removal with compatible entry redirection, and integrated mod ISO construction. New-level loading, portals and inherited quest state still need Xemu runtime validation.

Version **2.1.1** separates level and cinematic browsing and corrects D2 directional-light orientation and initial interior framing. It preserves the source mauve cavity shell and green outer cube enclosure; D2 does not declare a dedicated sky pass.

Future work:

- Allocate new native model, texture and scene records with valid references and archive relocation.
- Export new instances and arbitrary mesh topology to the game.
- Update collision geometry and related gameplay links alongside visual edits.
- Expand cubemap, animated texture and unsupported surface replacement.
- Validate skeletal animations, particles and runtime materials against gameplay.
- Validate custom-level loading, return portals and inherited quest state in Xemu.

These require retail-loader and in-game validation before game export is advertised. No release date is promised.

## Français

La **2.1.1** sépare les catalogues de niveaux et de cinématiques, corrige l’orientation des lumières directionnelles de D2 et son cadrage intérieur initial. L’enveloppe mauve de la cavité et l’enveloppe cubique extérieure verte source sont conservées ; D2 ne déclare pas de passe de ciel dédiée.

La **2.0.0** ajoute les imports, remplacements et duplications dans le projet, avec export des remplacements compatibles dans les ressources existantes. La **2.1.0** ajoute la création de niveaux depuis un modèle source, la suppression réversible avec redirection compatible et la construction ISO intégrée. Les prochaines étapes concernent la validation des nouveaux niveaux dans Xemu, l’ajout de nouvelles ressources au jeu, les collisions et les animations. Les nouveaux modèles et duplications restent actuellement des aperçus clairement indiqués.
