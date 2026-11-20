#!/bin/sh
#
#
while true
do
	pgrep "/release/bin/demo" > /dev/null;
	if [ $? -ne 0 ] 
   	then
		reboot
	fi
	sleep 10
done
