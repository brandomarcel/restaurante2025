## Restaurante BMARC

Sistema de restaurante

#### License

 bench --site restaurante_bmarc migrate
 sudo supervisorctl restart all
 pm2 start ecosystem.config.js --env production