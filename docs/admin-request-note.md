*Brouillon, à adapter avant envoi.*

**Objet : Autorisation d'une application tierce (lecture seule Gmail/Drive) pour le connecteur Claude de la Communauté**

Bonjour,

Pour l'équipe Claude de la Communauté, nous mettons en place un connecteur qui permet à Claude de lire, à la demande de chaque utilisateur, le contenu de ses pièces jointes Gmail et de ses fichiers Drive. Pour que les comptes de votre domaine puissent s'y connecter, merci d'autoriser l'application dans la console d'administration :

**Sécurité → Accès et contrôle des données → Commandes des API → Contrôle des accès des applications → Configurer une nouvelle application**, en recherchant l'ID client OAuth ci-dessous.

- **Nom de l'application :** Chemin Neuf – Claude connector
- **ID client OAuth :** `<à compléter>`
- **Accès demandés :** `gmail.readonly` et `drive.readonly` (**lecture seule**), plus l'adresse e-mail et le profil. Aucune écriture, aucun envoi, aucune suppression.
- **Niveau d'accès souhaité :** *Spécifique* à ces deux champs d'application (pas « Approuvée » sans restriction).
- **Hébergement :** serveur de la Communauté (Coolify, Europe), code source maîtrisé par nous.
- **Données conservées :** uniquement les jetons OAuth, chiffrés. Aucun contenu d'e-mail ou de fichier n'est enregistré ; les journaux ne contiennent ni contenu ni objet.
- **Qui peut l'utiliser :** uniquement les comptes Google Workspace des domaines autorisés, et les adresses ajoutées explicitement. Les autres comptes sont refusés dès la connexion.
- **Révocation :** chaque utilisateur peut la retirer (réglages Claude et https://myaccount.google.com/permissions) ; vous pouvez aussi bloquer l'application à tout moment depuis la même page de la console.

Je reste disponible pour toute question.

Bien à vous,
<signature>
