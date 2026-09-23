# Connecteur Claude – Google Workspace : confidentialité

*Texte source de la page publiée `cn_extras/static/privacy/index.html` (servie sur `/privacy`). Garder les deux synchrones.*

*Complément à la [Politique de confidentialité et de protection des données de la Communauté du Chemin Neuf](https://dam.chemin-neuf.net/wp-content/uploads/2020/09/politique_confidentialite_ccn_fr.pdf) (v2.2), pour ce seul service. Brouillon à relire avant publication.*

## Français

**Ce que fait le connecteur.** À votre demande, il permet à Claude (assistant d'IA d'Anthropic, utilisé par la Communauté dans le cadre de son abonnement Claude Team) de lire le contenu de vos pièces jointes Gmail et de vos fichiers Google Drive : Docs, Sheets, Slides, PDF et fichiers Office. Claude peut ainsi les résumer ou en extraire des informations.

**Données Google utilisées.**
- Lecture seule de Gmail (`gmail.readonly`) et de Google Drive (`drive.readonly`), ainsi que votre adresse e-mail et votre nom.
- Le connecteur ne peut ni envoyer, ni modifier, ni supprimer quoi que ce soit.
- Il ne lit que ce que vous demandez à Claude de lire.

**Qui reçoit ces données.**
- Le contenu lu est transmis **à Claude (Anthropic)** pour répondre à votre demande, dans la conversation où vous l'avez demandé. Il est traité selon les conditions commerciales de l'abonnement Claude de la Communauté.
- Pour les documents scannés, et seulement si la reconnaissance de texte est activée, le fichier peut être envoyé à **Microsoft Azure AI Document Intelligence (région UE)**. Microsoft supprime le résultat dès la fin du traitement, et en tout cas sous 24 h.
- Aucune autre transmission. Aucune vente, aucune publicité, aucun usage pour entraîner des modèles d'IA.
- Ces traitements ont lieu **hors de l'Union européenne**, notamment aux États-Unis (serveur du connecteur, Anthropic), dans le cadre prévu par la politique générale de la Communauté.

**Ce qui est conservé sur notre serveur.**
- Uniquement vos jetons de connexion OAuth, chiffrés, sur un serveur de la Communauté hébergé chez Microsoft Azure aux États-Unis (région East US 2).
- Aucun contenu d'e-mail ni de fichier n'y est enregistré : les fichiers sont lus en mémoire, le temps de la requête.
- Les journaux techniques contiennent votre adresse, des identifiants de fichiers et des tailles, jamais de contenu ni d'objet de message.

**Qui peut l'utiliser.** Uniquement les comptes Google Workspace des domaines de la Communauté (cheminneuf.community, chemin-neuf.org, wyd2027.org) et quelques adresses expressément autorisées.

**Usage limité (Google API Services).** L'utilisation et le transfert à toute autre application des informations reçues des API Google respectent les [règles relatives aux données utilisateur des services d'API Google](https://developers.google.com/terms/api-services-user-data-policy), y compris les exigences d'usage limité (*Limited Use*).

**Révoquer l'accès, effacer vos données.**
- Déconnectez le connecteur dans les réglages de Claude, et retirez l'application sur https://myaccount.google.com/permissions.
- Sur demande, l'administrateur supprime immédiatement vos jetons enregistrés.
- Vos autres droits (accès, rectification, opposition) s'exercent comme indiqué dans la politique générale.

**Contact.** Communauté du Chemin Neuf – Secrétariat général – Protection des données, 59 montée du Chemin Neuf, 69005 Lyon.

## English

*Supplement to the Chemin Neuf Community's general privacy policy (linked above), for this service only.*

**What it does.** At your request, it lets Claude (Anthropic's AI assistant, used by the Community under its Claude Team subscription) read the content of your Gmail attachments and Google Drive files: Docs, Sheets, Slides, PDF and Office files. Claude can then summarise them or extract information.

**Google data used.**
- Read-only Gmail (`gmail.readonly`) and Google Drive (`drive.readonly`), plus your e-mail address and name.
- The connector cannot send, change or delete anything.
- It only reads what you ask Claude to read.

**Who receives this data.**
- The content read is sent **to Claude (Anthropic)** to answer your request, in the conversation where you asked for it. It is processed under the commercial terms of the Community's Claude subscription.
- For scanned documents, and only if text recognition is enabled, the file may be sent to **Microsoft Azure AI Document Intelligence (EU region)**. Microsoft deletes the result as soon as processing ends, and in any case within 24 h.
- Nothing else is shared: no sale, no advertising, no use for training AI models.
- This processing takes place **outside the European Union**, notably in the United States (the connector's server, Anthropic), within the framework of the Community's general policy.

**What is stored on our server.**
- Only your OAuth sign-in tokens, encrypted, on a Community server hosted on Microsoft Azure in the United States (East US 2 region).
- No e-mail or file content is stored there: files are read in memory for the duration of the request.
- Technical logs contain your address, file IDs and sizes, never content or subjects.

**Who can use it.** Only Google Workspace accounts of the Community's domains (cheminneuf.community, chemin-neuf.org, wyd2027.org) and a few explicitly allowed addresses.

**Limited Use (Google API Services).** Use and transfer to any other app of information received from Google APIs will adhere to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

**Revoking access, deleting your data.**
- Disconnect the connector in Claude's settings, and remove the app at https://myaccount.google.com/permissions.
- On request, the administrator deletes your stored tokens immediately.
- Your other rights (access, rectification, objection) are exercised as described in the general policy.

**Contact.** Communauté du Chemin Neuf – Secrétariat général – Protection des données, 59 montée du Chemin Neuf, 69005 Lyon, France.
