# Connecteur Claude – Google Workspace (Communauté du Chemin Neuf)

*Brouillon à relire avant publication. / Draft to review before publishing.*

## Français

**Ce que fait le connecteur.** Il permet à Claude, à votre demande, de lire le contenu de vos pièces jointes Gmail et de vos fichiers Google Drive (Docs, Sheets, Slides, PDF, fichiers Office), afin de les résumer ou d'en extraire des informations.

**Accès demandés.** Lecture seule de Gmail (`gmail.readonly`) et de Google Drive (`drive.readonly`), ainsi que votre adresse e-mail et votre nom. Le connecteur ne peut ni envoyer, ni modifier, ni supprimer quoi que ce soit.

**Qui peut l'utiliser.** Uniquement les comptes des domaines de la Communauté (cheminneuf.community, chemin-neuf.org, wyd2027.org) et les adresses expressément autorisées.

**Ce qui est conservé.** Uniquement les jetons de connexion OAuth, chiffrés, sur un serveur de la Communauté hébergé en Europe. Aucun contenu d'e-mail ni de fichier n'est enregistré : les fichiers sont lus en mémoire, le temps de la requête. Les journaux techniques contiennent votre adresse, des identifiants de fichiers et des tailles, jamais de contenu ni d'objet de message.

**Reconnaissance de texte (si activée).** Pour les documents scannés, le fichier peut être envoyé à Microsoft Azure AI Document Intelligence (région UE) pour la reconnaissance de texte. Le résultat est supprimé chez Microsoft dès la fin du traitement, et en tout cas sous 24 h.

**Révoquer l'accès.** Déconnectez le connecteur dans les réglages de Claude, et retirez l'application sur https://myaccount.google.com/permissions. Sur demande, l'administrateur supprime immédiatement vos jetons enregistrés.

**Contact.** <adresse de contact>

## English

**What it does.** At your request, it lets Claude read the content of your Gmail attachments and Google Drive files (Docs, Sheets, Slides, PDF, Office files) to summarise them or extract information.

**Access requested.** Read-only Gmail (`gmail.readonly`) and Google Drive (`drive.readonly`), plus your e-mail address and name. The connector cannot send, change or delete anything.

**Who can use it.** Only accounts of the Community's domains (cheminneuf.community, chemin-neuf.org, wyd2027.org) and explicitly allowed addresses.

**What is stored.** Only OAuth sign-in tokens, encrypted, on a Community server hosted in Europe. No e-mail or file content is saved: files are read in memory for the duration of the request. Technical logs contain your address, file IDs and sizes, never content or subjects.

**Text recognition (if enabled).** For scanned documents, the file may be sent to Microsoft Azure AI Document Intelligence (EU region) for text recognition. Microsoft deletes the result as soon as processing ends, and in any case within 24 h.

**Revoking access.** Disconnect the connector in Claude's settings, and remove the app at https://myaccount.google.com/permissions. On request, the administrator deletes your stored tokens immediately.

**Contact.** <contact address>
