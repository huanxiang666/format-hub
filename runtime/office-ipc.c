/* Keep LibreOffice's hardcoded IPC socket inside this job's private directory.
 * Landlock remains authoritative for all actual filesystem access. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
static const char *redirect(const char *path, char *buffer, size_t size) {
    const char *root = getenv("FORMAT_HUB_IPC_DIR");
    if (root && !strncmp(path,"/tmp/OSL_PIPE_",14)) {
        snprintf(buffer,size,"%s/%s",root,path+5);
        return buffer;
    }
    return path;
}
int bind(int fd,const struct sockaddr *addr,socklen_t length) {
    static int (*original)(int,const struct sockaddr *,socklen_t);
    if (!original) original=dlsym(RTLD_NEXT,"bind");
    if (addr && addr->sa_family==AF_UNIX) {
        struct sockaddr_un changed=*(const struct sockaddr_un *)addr;
        char buffer[108];
        const char *path=redirect(changed.sun_path,buffer,sizeof(buffer));
        if(path==buffer) { snprintf(changed.sun_path,sizeof(changed.sun_path),"%s",path); return original(fd,(struct sockaddr *)&changed,sizeof(changed)); }
    }
    return original(fd,addr,length);
}
int connect(int fd,const struct sockaddr *addr,socklen_t length) {
    static int (*original)(int,const struct sockaddr *,socklen_t);
    if (!original) original=dlsym(RTLD_NEXT,"connect");
    if (addr && addr->sa_family==AF_UNIX) {
        struct sockaddr_un changed=*(const struct sockaddr_un *)addr;
        char buffer[108];const char *path=redirect(changed.sun_path,buffer,sizeof(buffer));
        if(path==buffer) { snprintf(changed.sun_path,sizeof(changed.sun_path),"%s",path); return original(fd,(struct sockaddr *)&changed,sizeof(changed)); }
    }
    return original(fd,addr,length);
}
int unlink(const char *path) {
    static int (*original)(const char *);
    if(!original) original=dlsym(RTLD_NEXT,"unlink");
    char buffer[108];return original(redirect(path,buffer,sizeof(buffer)));
}
