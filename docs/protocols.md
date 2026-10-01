# Management interface

#### HTTP Polling

The earliest mechanism observed by this project. The alarm simply posts an XML document to an HTTP endpoint (in plain text) at a set interval configured on the alarm hub/panel. The same format document is returned by the server, which optionally may insert commands into it.

The document typically looks like this:

`<?xml version="1.0" encoding="ISO-8859-1"?>
<polling>
	<mac value="00:1D:94:02:55:E2"/>
	<sn value="00:1D:94:02:55:E2"/>
	<rptipid value="1234"/>
	<ver value="CTC-1735 1.0.24 I1735E23K"/>
	<lan value="192.168.0.58:80"/>
	<wan value=":53080"/>
	<port-fw value="0"/>
	<sensor_mod value="1"/>
	<commands>
		<command id="0" action="getPanel">
			<result>1</result>
			<message>OK</message>
			<xmldata>
				...
			</xmldata>
		</command>
	</commands>
</polling>`

The document contains status information, commands and results. When the server wishes to send a command to an alarm, it must wait for the alarm to "poll". The command is inserted into the polling document returned to the alarm. The server must then wait until next time the alarm "polls" to collect the result. Interacting with alarms this way is SLOW. It was probably fine in the days when the alarms were more likely to be managed over the phone by security companies.

The communications occurring over the public internet completely unencrypted is not ideal but that is how it was done back in 2000s when these systems were sold.

#### XMPP "Polling" protocol

This appears to be another fairly legacy approach used by Climax systems. These alarms use the exact same polling document thus the same message interpreter/parser code is used *however* the polling document is exchanged instantaneously over a persistent connection using the XMPP protocol. If the alarm has a status event to report, the server receives it instantly. If the server wishes to send a command it can fire a polling document at an alarm and collect the result instantly.

The only hubs using this protocol observed so far use XMPP-TLS but do not validate the server certificate thus can be used with this project without any hacks.

#### XMPP "ML" Protocol

This appears to be the current approach. The same outer XMPP transport is used but the inner control protocol is completely different. Rather than an XML document; exchanges with the server occur using simple text commands.

For example if the server wishes to arm the alarm it will send an XMPP Message stanza containing:

`<random 32 character string>:;MODA:1,1`

1 meaning "Arm". The alarm will reply with:

`<same random 32 char string>:OK;MODA:OK;`

The 32 character string is interesting. It changes with each request and looks suspiciously like an authentication token but the hub tested with this project does not validate it.
#### XMPP-TLS "ML" Alarms

Most of the Yale hubs in circulation today (EF/SR/Sync/HSA) are of this type (excluding the current "AL" product line where the OEM is not yet confirmed). The protocol is identical to the non TLS alarms but strict certificate checks performed by these hubs make it impossible to operate them on an unofficial server, or snoop the communications directly. Some were found to spit out the XMPP comms with the server in plain text from an RS-232 debug header on the PCB - confirming an identical protocol.
# CID Interface

All internet connected Climax alarms send real time notifications to the server using the SIA CID protocol. It is unencrypted and able to be intercepted regardless of the generation/type of the hub. For newer TLS hubs it is not possible to change the address of the report server due to inability to access the built-in web interface but (for example) a DNS override could be used to redirect messages.

This project provides a compatible CID server which drives an email notifier service.
# Upload interface

For hubs with PIR cameras attached, all captured media is uploaded to the server via an unencrypted HTTP POST request.

This project provides an upload endpoint and backend/frontend implementation for captured media.
