FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 3000
RUN sed -i 's/listen       80;/listen       3000;/' /etc/nginx/conf.d/default.conf
