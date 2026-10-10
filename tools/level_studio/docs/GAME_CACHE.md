# Testing a modified Azurik disc

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

This note refers **only to the Xbox game cache partitions**. Cached level files can keep a changed disc from appearing as expected. It does not refer to deleting the virtual hard-drive image, the application's projects, or the E partition containing your saved games.

1. Use **Clear Cache** in xemu-dashboard or **Flush Cache Partitions** in LithiumX.
2. Restart the game with your modified ISO selected.
3. Load an ordinary in-game save. An old emulator snapshot can retain the previously loaded world state.

The editor does not clear the emulator's cache or modify saves automatically. See [Xemu troubleshooting](https://xemu.app/docs/troubleshooting/), [where saves are stored](https://xemu.app/docs/faq/), and [snapshot behavior](https://xemu.app/docs/snapshots/).

## Français

Si vos modifications ne s'affichent pas en jeu, videz uniquement les partitions de cache Xbox avec **Clear Cache** dans xemu-dashboard ou **Flush Cache Partitions** dans LithiumX, puis redémarrez le jeu sur votre ISO modifiée. Les sauvegardes du jeu sont conservées sur la partition E, distincte de ce cache. Conservez votre disque dur virtuel et chargez une sauvegarde du jeu plutôt qu'un ancien état instantané de l'émulateur.

La restauration d'un niveau dans l'éditeur remet les modifications du projet à leur état source ; cette opération est distincte du cache du jeu.
